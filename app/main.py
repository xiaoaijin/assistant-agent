"""HTTP 层：顾客聊天（SSE 流式）、会话查询、坐席审批与交还、崩溃恢复入口。"""

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import approvals, db, loop, state, store
from .errors import Conflict, ConversationTransferred, NotFound


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(title="电商客服助手", lifespan=lifespan)


@app.exception_handler(NotFound)
@app.exception_handler(ConversationTransferred)
async def _not_found_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(Conflict)
async def _conflict_handler(request: Request, exc: Conflict):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


def _uuid_or_404(conv_id: str):
    from uuid import UUID

    try:
        return UUID(conv_id)
    except ValueError:
        raise NotFound("会话不存在")


class CreateConversationIn(BaseModel):
    customer_id: str
    shop_id: str
    channel: str = "web"


class MessageIn(BaseModel):
    text: str


@app.post("/api/conversations")
async def create_conversation(body: CreateConversationIn):
    conv = await store.create_conversation(body.customer_id, body.shop_id, body.channel)
    return {"id": str(conv["id"]), "status": conv["status"]}


@app.get("/api/conversations/{conv_id}/messages")
async def get_messages(conv_id: str):
    rows = await store.get_messages(conv_id)
    return [
        {"sequence": r["sequence"], "role": r["role"], "content": r["content"]}
        for r in rows
    ]


@app.get("/api/conversations/{conv_id}/audit")
async def get_audit(conv_id: str):
    rows = await store.list_actions(conv_id)
    return [
        {
            "seq": r["seq"], "actor": r["actor"], "action": r["action"],
            "detail": r["detail"], "step": r["step"],
        }
        for r in rows
    ]


@app.get("/api/conversations/{conv_id}/state")
async def get_state(conv_id: str):
    conv = await store.get_conversation(conv_id)
    if conv is None:
        raise NotFound("会话不存在")
    actions = [
        {"actor": r["actor"], "action": r["action"], "step": r["step"]}
        for r in await store.list_actions(conv_id)
    ]
    pending_row = await store.get_active_pending(conv_id)
    pending = (
        {
            "id": pending_row["id"], "amount": float(pending_row["amount"] or 0),
            "status": pending_row["status"], "owner": pending_row["owner"],
        }
        if pending_row else None
    )
    return state.derive_state(conv["status"], actions, pending)


def _sse_generator(conv_id: str, coro_factory) -> StreamingResponse:
    async def gen():
        q: asyncio.Queue = asyncio.Queue()

        def emit(text: str) -> None:
            q.put_nowait(("delta", {"text": text}))

        async def run():
            try:
                result = await coro_factory(emit)
                await q.put(("done", result))
            except (ConversationTransferred, NotFound, Conflict) as e:
                await q.put(("error", {"message": str(e)}))
            except Exception as e:  # 模型/网络等运行时错误也要送达客户端
                await q.put(("error", {"message": f"回合中断：{type(e).__name__}: {e}"}))
            finally:
                q.put_nowait(None)

        task = asyncio.create_task(run())
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                kind, payload = item
                yield f"data: {json.dumps({'type': kind, **payload}, ensure_ascii=False)}\n\n"
        finally:
            # 客户端断开不中断回合：循环状态全部落库，结果经由 DB 可查（ADR-0002）
            pass

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/conversations/{conv_id}/messages")
async def post_message(conv_id: str, body: MessageIn):
    _uuid_or_404(conv_id)

    async def factory(emit):
        return await loop.run_turn(conv_id, body.text, emit)

    return _sse_generator(conv_id, factory)


@app.post("/api/conversations/{conv_id}/resume")
async def resume_conversation(conv_id: str):
    """崩溃恢复入口：幂等，任何时刻可调用。"""
    _uuid_or_404(conv_id)

    async def factory(emit):
        return await loop.resume_loop(conv_id, emit)

    return _sse_generator(conv_id, factory)


@app.get("/api/approvals")
async def list_approvals(status: str | None = None):
    rows = await store.list_pendings(status)
    return [
        {
            "id": r["id"], "conversation_id": str(r["conversation_id"]),
            "tool_name": r["tool_name"], "arguments": r["arguments"],
            "amount": float(r["amount"] or 0), "status": r["status"], "owner": r["owner"],
        }
        for r in rows
    ]


@app.post("/api/approvals/{approval_id}/approve")
async def approve_approval(approval_id: str):
    return await approvals.approve(approval_id)


@app.post("/api/approvals/{approval_id}/reject")
async def reject_approval(approval_id: str):
    return await approvals.reject(approval_id)


@app.post("/api/conversations/{conv_id}/handback")
async def handback_conversation(conv_id: str):
    _uuid_or_404(conv_id)
    return await approvals.handback(conv_id)
