"""HTTP 层：顾客聊天（SSE 流式）、会话查询、坐席审批与交还、崩溃恢复入口。"""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import approvals, db, loop, scheduler, state, store, stream
from .admin_api import router as admin_router
from .config import settings
from .errors import Conflict, ConversationTransferred, NotFound


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    scheduler.start(settings.database_url)
    await stream.connect()
    yield
    await stream.close()
    scheduler.shutdown()
    await db.close_pool()


app = FastAPI(title="电商客服助手", lifespan=lifespan)
app.include_router(admin_router)


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


class ReminderIn(BaseModel):
    kind: str
    run_at: datetime


@app.post("/api/conversations")
async def create_conversation(body: CreateConversationIn):
    conv = await store.create_conversation(body.customer_id, body.shop_id, body.channel)
    return {"id": str(conv["id"]), "status": conv["status"]}


@app.get("/api/conversations")
async def list_conversations(status: str | None = None):
    """坐席工作台会话列表：最近消息倒序，未决待审批项摘要随行。"""
    now = datetime.now(timezone.utc)
    rows = await store.list_conversations(status)
    return [
        {
            "id": str(r["id"]), "customer_id": r["customer_id"],
            "shop_id": str(r["shop_id"]), "channel": r["channel"],
            "status": r["status"], "created_at": r["created_at"].isoformat(),
            "last_message_at": r["last_message_at"].isoformat() if r["last_message_at"] else None,
            "pending_approval": {
                "id": r["pending_id"], "amount": float(r["pending_amount"] or 0),
                "sla_deadline": r["pending_sla_deadline"].isoformat() if r["pending_sla_deadline"] else None,
                "escalated": state.escalation_status(now, r["pending_sla_deadline"], r["pending_escalated_at"]) == "escalated",
            } if r["pending_id"] else None,
        }
        for r in rows
    ]


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


@app.post("/api/conversations/{conv_id}/reminders")
async def create_reminder(conv_id: str, body: ReminderIn):
    """催付/回访提醒：到点由调度器往会话发一条助手消息（SQL jobstore 持久化）。"""
    _uuid_or_404(conv_id)
    try:
        job_id = scheduler.schedule_reminder(scheduler.current(), conv_id, body.kind, body.run_at)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"job_id": job_id}


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
                event_id = await stream.add_event(conv_id, {"type": kind, **payload})
                id_line = f"id: {event_id}\n" if event_id else ""
                yield f"{id_line}data: {json.dumps({'type': kind, **payload}, ensure_ascii=False)}\n\n"
        finally:
            # 客户端断开不中断回合：循环状态全部落库，结果经由 DB 可查（ADR-0002）
            pass

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/conversations/{conv_id}/messages")
async def post_message(conv_id: str, body: MessageIn):
    _uuid_or_404(conv_id)
    await stream.start_turn(conv_id)  # 新回合：清空检查点流

    async def factory(emit):
        return await loop.run_turn(conv_id, body.text, emit)

    return _sse_generator(conv_id, factory)


@app.get("/api/conversations/{conv_id}/stream")
async def stream_conversation(conv_id: str, request: Request):
    """断线续传：带 Last-Event-ID 重连，从 Redis Stream 检查点重放并跟随到 done。"""
    _uuid_or_404(conv_id)
    last_id = request.headers.get("last-event-id", "0")

    async def gen():
        async for e in stream.replay(conv_id, last_id):
            yield f"id: {e['id']}\ndata: {json.dumps(e['payload'], ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


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
    now = datetime.now(timezone.utc)
    return [
        {
            "id": r["id"], "conversation_id": str(r["conversation_id"]),
            "tool_name": r["tool_name"], "arguments": r["arguments"],
            "amount": float(r["amount"] or 0), "status": r["status"], "owner": r["owner"],
            "sla_deadline": r["sla_deadline"].isoformat() if r["sla_deadline"] else None,
            "escalated": state.escalation_status(now, r["sla_deadline"], r["escalated_at"]) == "escalated",
        }
        for r in rows
    ]


@app.post("/api/approvals/sweep")
async def sweep_approvals():
    """超 SLA 待审批项升级（坐席工作台/定时任务触发；幂等）。"""
    return await approvals.sweep_overdue(datetime.now(timezone.utc))


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
