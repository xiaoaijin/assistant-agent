"""坐席侧：审批待审批项、交还会话。

与顾客回合共享同一把 per-conversation advisory lock，互斥写入。
退款执行用 step key = execute:{approval_id} 保证恰好一次。
"""

from typing import Any

from . import db, store, tools
from .errors import Conflict, NotFound
from .state import can_handback


async def approve(approval_id: str) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", approval_id)
            p = await store.get_pending_conn(conn, approval_id)
            if p is None:
                raise NotFound("待审批项不存在")
            if p["status"] != "pending":
                raise Conflict(f"待审批项已处理（当前：{p['status']}）")
            conv_id = str(p["conversation_id"])
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            await store.set_pending_status(conn, approval_id, "approved", owner="seat")
            await store.record(conn, conv_id, "seat", "approve", {"approval_id": approval_id})

            # 恰好一次执行：重启/重试读步骤账本，不重复退款
            step_key = f"execute:{approval_id}"
            existing = await store.get_step_conn(conn, conv_id, step_key)
            if existing is not None:
                result = dict(existing["step_result"])
            else:
                args = p["arguments"] or {}
                result = await tools.execute_refund(
                    conn, conv_id, args.get("order_id", ""), float(p["amount"] or 0)
                )
                await store.record(
                    conn, conv_id, "system", "refund_executed",
                    detail={"approval_id": approval_id}, step=step_key, step_result=result,
                )
            await store.set_pending_status(conn, approval_id, "executed")
            await store.set_ticket_status(conn, conv_id, "resolved")

            text = f"您的退款 ¥{float(p['amount']):g} 已处理完成，请注意查收。"
            seq = await store.add_message(conn, conv_id, "assistant", {"text": text})
            await store.record(
                conn, conv_id, "assistant", "reply", {"text": text},
                step=f"reply:{seq}", step_result={"text": text},
            )
    return result


async def reject(approval_id: str) -> dict:
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", approval_id)
            p = await store.get_pending_conn(conn, approval_id)
            if p is None:
                raise NotFound("待审批项不存在")
            if p["status"] != "pending":
                raise Conflict(f"待审批项已处理（当前：{p['status']}）")
            conv_id = str(p["conversation_id"])
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            await store.set_pending_status(conn, approval_id, "rejected", owner="seat")
            await store.record(conn, conv_id, "seat", "reject", {"approval_id": approval_id})

            text = f"经坐席审核，您申请的 ¥{float(p['amount']):g} 退款未能通过。如有疑问欢迎继续沟通。"
            seq = await store.add_message(conn, conv_id, "assistant", {"text": text})
            await store.record(
                conn, conv_id, "assistant", "reply", {"text": text},
                step=f"reply:{seq}", step_result={"text": text},
            )
    return {"status": "rejected", "approval_id": approval_id}


async def handback(conv_id: str) -> dict:
    """坐席处理完成，会话交还助手。规则见 state.can_handback。"""
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            conv = await store.get_conversation_conn(conn, conv_id)
            if conv is None:
                raise NotFound("会话不存在")
            pending = await conn.fetchrow(
                "SELECT status FROM pending_approvals WHERE conversation_id = $1 AND status = 'pending'",
                db._uuid(conv_id),
            )
            ok, reason = can_handback(conv["status"], pending["status"] if pending else None)
            if not ok:
                raise Conflict(reason)
            await store.set_conversation_status(conn, conv_id, "ai_serving")
            await conn.execute(
                "UPDATE tickets SET status = 'resolved', updated_at = now() "
                "WHERE conversation_id = $1 AND status = 'processing'",
                db._uuid(conv_id),
            )
            await store.record(conn, conv_id, "seat", "hand_back", {})
    return {"status": "ai_serving"}
