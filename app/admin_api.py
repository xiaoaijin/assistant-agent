"""管理端 API：登录、店铺管理（超管）、知识文档 CRUD（同步摄取）、冲突队列裁决（ADR-0005）。

鉴权：HMAC 签名 token（app/admin_auth），Authorization: Bearer。
数据隔离：admin_users.shop_id 为空即平台超管可跨店；普通管理员全部查询限定本店。
"""

import uuid as uuid_mod
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from . import db
from .admin_auth import sign_token, verify_password, verify_token
from .config import settings
from .rag.ingest import ingest_document
from .rag.store import (
    create_shop,
    get_document,
    list_chunks,
    list_documents,
    set_document_status,
)

router = APIRouter(prefix="/api/admin")


def _current_admin(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    claims = verify_token(token, settings.admin_secret) if token else None
    if claims is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return claims


def _require_super(admin: dict) -> None:
    if admin.get("shop_id") is not None:
        raise HTTPException(status_code=403, detail="仅平台超管可执行此操作")


class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginIn) -> dict:
    row = await db.fetchrow("SELECT * FROM admin_users WHERE username = $1", body.username)
    if row is None or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    claims = {
        "sub": body.username,
        "shop_id": str(row["shop_id"]) if row["shop_id"] else None,
    }
    return {"token": sign_token(claims, settings.admin_secret, ttl_seconds=12 * 3600)}


@router.post("/shops")
async def create_shop_endpoint(body: dict, admin: dict = Depends(_current_admin)) -> dict:
    _require_super(admin)
    row = await create_shop(body["name"])
    return {"id": str(row["id"]), "name": row["name"]}


@router.get("/shops")
async def list_shops(admin: dict = Depends(_current_admin)) -> list[dict]:
    _require_super(admin)
    rows = await db.fetch("SELECT id, name FROM shops ORDER BY created_at")
    return [{"id": str(r["id"]), "name": r["name"]} for r in rows]


def _visible_shop_ids(admin: dict, requested: str | None) -> str | None:
    """普通管理员只能看本店；超管可按参数过滤或看全部。"""
    if admin.get("shop_id") is None:
        return requested
    if requested and requested != admin["shop_id"]:
        raise HTTPException(status_code=403, detail="无权访问该店铺")
    return admin["shop_id"]


@router.get("/knowledge/documents")
async def documents(
    shop_id: str | None = None, status: str | None = None,
    admin: dict = Depends(_current_admin),
) -> list[dict]:
    visible = _visible_shop_ids(admin, shop_id)
    rows = await list_documents(visible, status)
    return [
        {
            "id": str(r["id"]), "shop_id": str(r["shop_id"]), "content_type": r["content_type"],
            "title": r["title"], "source_key": r["source_key"], "filename": r["filename"],
            "status": r["status"], "created_by": r["created_by"],
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


@router.post("/knowledge/documents")
async def upload_document(
    request: Request,
    file: UploadFile,
    shop_id: str = Form(...),
    source_key: str = Form(...),
    title: str = Form(...),
    content_type: str = Form("policy"),
    admin: dict = Depends(_current_admin),
) -> dict:
    visible = _visible_shop_ids(admin, shop_id)
    data = await file.read()
    try:
        return await ingest_document(
            shop_id=visible, content_type=content_type, source_key=source_key,
            title=title, filename=file.filename or "", data=data,
            created_by=admin["sub"], embedder=_embedder(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _embedder(request: Request) -> Any:
    """嵌入客户端可注入：测试设置 app.state.embedder_factory，默认生产实现。"""
    factory = getattr(request.app.state, "embedder_factory", None)
    if factory is not None:
        return factory()
    from .rag.embeddings import LiteLLMEmbedder

    return LiteLLMEmbedder()


@router.delete("/knowledge/documents/{document_id}")
async def delete_document(document_id: str, admin: dict = Depends(_current_admin)) -> dict:
    doc = await get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    _visible_shop_ids(admin, str(doc["shop_id"]))
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM knowledge_documents WHERE id = $1", db.uuid(document_id))
    return {"deleted": True}


@router.get("/knowledge/documents/{document_id}/chunks")
async def document_chunks(document_id: str, admin: dict = Depends(_current_admin)) -> list[dict]:
    doc = await get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    _visible_shop_ids(admin, str(doc["shop_id"]))
    rows = await list_chunks(document_id)
    return [
        {
            "seq": r["seq"], "text": r["text"], "title_path": r["title_path"],
            "page": r["page"], "char_count": r["char_count"],
        }
        for r in rows
    ]


@router.get("/knowledge/conflicts")
async def conflicts(shop_id: str | None = None, admin: dict = Depends(_current_admin)) -> list[dict]:
    visible = _visible_shop_ids(admin, shop_id)
    rows = await list_documents(visible, "pending_review")
    return [
        {
            "id": str(r["id"]), "title": r["title"], "source_key": r["source_key"],
            "content_type": r["content_type"], "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


class ResolveIn(BaseModel):
    decision: str  # adopt_new | reject_new


@router.post("/knowledge/documents/{document_id}/resolve")
async def resolve_conflict(document_id: str, body: ResolveIn, admin: dict = Depends(_current_admin)) -> dict:
    doc = await get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    _visible_shop_ids(admin, str(doc["shop_id"]))
    if doc["status"] != "pending_review":
        raise HTTPException(status_code=409, detail="文档不在冲突队列中")
    if body.decision not in ("adopt_new", "reject_new"):
        raise HTTPException(status_code=422, detail="decision 必须是 adopt_new 或 reject_new")

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            if body.decision == "adopt_new":
                await set_document_status(conn, document_id, "active")
                # 只让触发冲突的旧文档让位（摄取时记录在 conflict_with）
                await conn.execute(
                    """
                    UPDATE knowledge_documents
                    SET status = 'superseded', updated_at = now()
                    WHERE id = ANY(
                        SELECT (value #>> '{}')::uuid FROM jsonb_array_elements($1::jsonb) AS value
                    )
                    """,
                    doc["conflict_with"] or [],
                )
            else:
                await set_document_status(conn, document_id, "rejected")
    return {"id": document_id, "status": "active" if body.decision == "adopt_new" else "rejected"}
