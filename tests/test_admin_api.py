"""S4 管理 API seam：HTTP 行为（登录/鉴权/店隔离/文档 CRUD/冲突裁决/店铺管理）。

用 httpx ASGITransport 直连 app（不走 lifespan，池由 db_pool fixture 提供）。
"""

import httpx
import pytest

from app import db
from app.admin_auth import hash_password
from app.main import app
from app.rag.ingest import ingest_document
from app.rag.store import create_shop
from tests.fake_embedder import FakeEmbedder, cos_pair, unit


@pytest.fixture
async def client(db_pool):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _mk_admin(username: str, password: str, shop_id: str | None) -> str:
    async with db.pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO admin_users (username, password_hash, shop_id) VALUES ($1, $2, $3) RETURNING id",
            username, hash_password(password), db.uuid(shop_id) if shop_id else None,
        )
    return str(row["id"])


async def _login(client: httpx.AsyncClient, username: str, password: str):
    return await client.post("/api/admin/login", json={"username": username, "password": password})


def _auth(client: httpx.AsyncClient, token: str):
    client.headers["Authorization"] = f"Bearer {token}"


MD = "# 政策\n\n七天无理由退货适用于所有自营商品。\n".encode()


async def test_login_success_returns_token_and_wrong_password_is_401(client):
    await _mk_admin("alice", "pw1", None)
    resp = await _login(client, "alice", "pw1")
    assert resp.status_code == 200
    assert "token" in resp.json()
    assert (await _login(client, "alice", "wrong")).status_code == 401
    assert (await _login(client, "ghost", "pw1")).status_code == 401


async def test_documents_endpoints_require_auth(client):
    assert (await client.get("/api/admin/knowledge/documents")).status_code == 401
    resp = await client.post(
        "/api/admin/knowledge/documents",
        files={"file": ("a.md", MD)},
        data={"source_key": "a", "title": "A", "content_type": "policy", "shop_id": "x"},
    )
    assert resp.status_code == 401


async def test_shop_admin_sees_only_own_shop_documents(client):
    shop_a, shop_b = await create_shop("A"), await create_shop("B")
    await _mk_admin("a-admin", "pw", str(shop_a["id"]))
    await ingest_document(
        shop_id=str(shop_a["id"]), content_type="policy", source_key="ka",
        title="A 的政策", filename="a.md", data=MD, created_by="a",
        embedder=FakeEmbedder({"政策\n七天无理由退货适用于所有自营商品。": unit(0)}),
    )
    await ingest_document(
        shop_id=str(shop_b["id"]), content_type="policy", source_key="kb",
        title="B 的政策", filename="b.md", data=MD, created_by="b",
        embedder=FakeEmbedder({"政策\n七天无理由退货适用于所有自营商品。": unit(1)}),
    )
    _auth(client, (await _login(client, "a-admin", "pw")).json()["token"])
    docs = (await client.get("/api/admin/knowledge/documents")).json()
    assert [d["title"] for d in docs] == ["A 的政策"]


async def test_document_create_and_chunk_preview(client):
    shop = await create_shop("A")
    await _mk_admin("a-admin", "pw", str(shop["id"]))
    _auth(client, (await _login(client, "a-admin", "pw")).json()["token"])
    app.state.embedder_factory = lambda: FakeEmbedder(
        {"政策\n七天无理由退货适用于所有自营商品。": unit(0)}
    )
    resp = await client.post(
        "/api/admin/knowledge/documents",
        files={"file": ("policy.md", MD)},
        data={"source_key": "p1", "title": "售后政策", "content_type": "policy", "shop_id": str(shop["id"])},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"
    doc_id = resp.json()["document_id"]

    docs = (await client.get("/api/admin/knowledge/documents")).json()
    assert len(docs) == 1
    chunks = (await client.get(f"/api/admin/knowledge/documents/{doc_id}/chunks")).json()
    assert len(chunks) == 1
    assert "七天无理由" in chunks[0]["text"]


async def test_document_delete_removes_document_and_chunks(client):
    shop = await create_shop("A")
    await _mk_admin("a-admin", "pw", str(shop["id"]))
    _auth(client, (await _login(client, "a-admin", "pw")).json()["token"])
    app.state.embedder_factory = lambda: FakeEmbedder(
        {"政策\n七天无理由退货适用于所有自营商品。": unit(0)}
    )
    doc_id = (
        await client.post(
            "/api/admin/knowledge/documents",
            files={"file": ("policy.md", MD)},
            data={"source_key": "p1", "title": "售后政策", "content_type": "policy", "shop_id": str(shop["id"])},
        )
    ).json()["document_id"]

    resp = await client.delete(f"/api/admin/knowledge/documents/{doc_id}")
    assert resp.status_code == 200
    assert (await client.get("/api/admin/knowledge/documents")).json() == []
    assert (await client.get(f"/api/admin/knowledge/documents/{doc_id}/chunks")).status_code == 404


async def _seed_conflict(shop_id: str) -> None:
    base = unit(0)
    await ingest_document(
        shop_id=shop_id, content_type="policy", source_key="old",
        title="旧政策", filename="old.md",
        data="# 政策\n\n退货时限为七天。\n".encode(), created_by="a",
        embedder=FakeEmbedder({"政策\n退货时限为七天。": base}),
    )
    await ingest_document(
        shop_id=shop_id, content_type="policy", source_key="new",
        title="新政策", filename="new.md",
        data="# 新政策\n\n退货时限为三十天。\n".encode(), created_by="a",
        embedder=FakeEmbedder({"新政策\n退货时限为三十天。": cos_pair(base, unit(1), 0.9)}),
    )


async def test_conflict_adopt_spares_unrelated_same_type_documents(client):
    """裁决只影响触发冲突的旧文档，同类型无关文档保持 active。"""
    shop = await create_shop("A")
    await _mk_admin("a-admin", "pw", str(shop["id"]))
    await _seed_conflict(str(shop["id"]))
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="unrelated",
        title="无关政策", filename="u.md",
        data="# 运费\n\n偏远地区加收 10 元。\n".encode(), created_by="a",
        embedder=FakeEmbedder({"运费\n偏远地区加收 10 元。": unit(9)}),
    )
    _auth(client, (await _login(client, "a-admin", "pw")).json()["token"])
    new_doc = (await client.get("/api/admin/knowledge/conflicts")).json()[0]["id"]
    await client.post(f"/api/admin/knowledge/documents/{new_doc}/resolve", json={"decision": "adopt_new"})
    docs = {d["title"]: d["status"] for d in (await client.get("/api/admin/knowledge/documents")).json()}
    assert docs["无关政策"] == "active"
    assert docs["旧政策"] == "superseded"
    assert docs["新政策"] == "active"


async def test_conflict_queue_adopt_new_supersedes_old(client):
    shop = await create_shop("A")
    await _mk_admin("a-admin", "pw", str(shop["id"]))
    await _seed_conflict(str(shop["id"]))
    _auth(client, (await _login(client, "a-admin", "pw")).json()["token"])

    conflicts = (await client.get("/api/admin/knowledge/conflicts")).json()
    assert [c["title"] for c in conflicts] == ["新政策"]
    new_doc = conflicts[0]["id"]

    resp = await client.post(f"/api/admin/knowledge/documents/{new_doc}/resolve", json={"decision": "adopt_new"})
    assert resp.status_code == 200
    docs = {d["title"]: d["status"] for d in (await client.get("/api/admin/knowledge/documents")).json()}
    assert docs["新政策"] == "active"
    assert docs["旧政策"] == "superseded"


async def test_conflict_queue_reject_new_keeps_old_active(client):
    shop = await create_shop("A")
    await _mk_admin("a-admin", "pw", str(shop["id"]))
    await _seed_conflict(str(shop["id"]))
    _auth(client, (await _login(client, "a-admin", "pw")).json()["token"])

    new_doc = (await client.get("/api/admin/knowledge/conflicts")).json()[0]["id"]
    resp = await client.post(f"/api/admin/knowledge/documents/{new_doc}/resolve", json={"decision": "reject_new"})
    assert resp.status_code == 200
    docs = {d["title"]: d["status"] for d in (await client.get("/api/admin/knowledge/documents")).json()}
    assert docs["新政策"] == "rejected"
    assert docs["旧政策"] == "active"


async def test_shop_management_is_superadmin_only(client):
    shop = await create_shop("A")
    await _mk_admin("shop-admin", "pw", str(shop["id"]))
    await _mk_admin("super", "pw", None)

    _auth(client, (await _login(client, "shop-admin", "pw")).json()["token"])
    assert (await client.post("/api/admin/shops", json={"name": "B 店"})).status_code == 403

    _auth(client, (await _login(client, "super", "pw")).json()["token"])
    resp = await client.post("/api/admin/shops", json={"name": "B 店"})
    assert resp.status_code == 200
    assert (await client.get("/api/admin/shops")).json()[-1]["name"] == "B 店"
