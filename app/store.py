"""数据访问层：消息、动作留痕、待审批项、工单、额度。

写路径都要求调用方已持有 per-conversation advisory lock 并处于事务中，
sequence/seq 用 MAX+1 分配，靠唯一键保证幂等。
"""

import json
import uuid
from typing import Any

from . import db


def _uuid(conv_id: str) -> uuid.UUID:
    return uuid.UUID(conv_id)


async def create_conversation(customer_id: str, shop_id: str, channel: str = "web") -> Any:
    return await db.fetchrow(
        "INSERT INTO conversations (customer_id, shop_id, channel) VALUES ($1, $2, $3) RETURNING *",
        customer_id, db.uuid(shop_id), channel,
    )


async def get_conversation(conv_id: str) -> Any:
    return await db.fetchrow("SELECT * FROM conversations WHERE id = $1", _uuid(conv_id))


async def list_conversations(status: str | None = None) -> list[Any]:
    """坐席工作台列表：按最近消息时间倒序（无消息用 created_at），带未决待审批项摘要。"""
    return await db.fetch(
        """
        SELECT c.id, c.customer_id, c.shop_id, c.channel, c.status, c.created_at,
               MAX(m.created_at) AS last_message_at,
               p.id AS pending_id, p.amount AS pending_amount,
               p.sla_deadline AS pending_sla_deadline, p.escalated_at AS pending_escalated_at
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        LEFT JOIN LATERAL (
            SELECT * FROM pending_approvals pa
            WHERE pa.conversation_id = c.id AND pa.status = 'pending'
            ORDER BY created_at DESC LIMIT 1
        ) p ON true
        WHERE ($1::text IS NULL OR c.status = $1)
        GROUP BY c.id, p.id, p.amount, p.sla_deadline, p.escalated_at
        ORDER BY COALESCE(MAX(m.created_at), c.created_at) DESC
        """,
        status,
    )


async def get_conversation_conn(conn: Any, conv_id: str) -> Any:
    return await conn.fetchrow("SELECT * FROM conversations WHERE id = $1", _uuid(conv_id))


async def set_conversation_status(conn: Any, conv_id: str, status: str) -> None:
    await conn.execute("UPDATE conversations SET status = $2 WHERE id = $1", _uuid(conv_id), status)


async def add_message(conn: Any, conv_id: str, role: str, content: dict) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO messages (conversation_id, sequence, role, content)
        SELECT $1, COALESCE(MAX(sequence), 0) + 1, $2, $3::jsonb FROM messages
        WHERE conversation_id = $1
        RETURNING sequence
        """,
        _uuid(conv_id), role, content,
    )
    return row["sequence"]


async def get_messages(conv_id: str) -> list[Any]:
    return await db.fetch(
        "SELECT * FROM messages WHERE conversation_id = $1 ORDER BY sequence",
        _uuid(conv_id),
    )


async def has_tool_result(conn: Any, conv_id: str, tool_call_id: str) -> bool:
    row = await conn.fetchrow(
        """
        SELECT 1 FROM messages
        WHERE conversation_id = $1 AND role = 'tool' AND content->>'tool_call_id' = $2
        """,
        _uuid(conv_id), tool_call_id,
    )
    return row is not None


async def unexecuted_tool_calls(conv_id: str) -> list[dict]:
    """未执行的工具调用（重启恢复入口）。

    同一时刻至多一个未完成回合：取最近一条带 tool_calls 的 assistant 消息，
    逐个对照步骤账本（actions.step），缺的就是待执行/待续的。
    """
    row = await db.fetchrow(
        """
        SELECT sequence, content FROM messages
        WHERE conversation_id = $1 AND role = 'assistant' AND content ? 'tool_calls'
        ORDER BY sequence DESC LIMIT 1
        """,
        _uuid(conv_id),
    )
    if row is None:
        return []
    calls = row["content"]["tool_calls"]
    out = []
    for i, c in enumerate(calls):
        step_key = f"tool:{row['sequence']}:{i}"
        if await get_step(conv_id, step_key) is None:
            out.append({
                "assistant_seq": row["sequence"],
                "index": i,
                "id": c.get("id") or f"{row['sequence']}-{i}",
                "name": c["name"],
                "arguments": c.get("arguments") or {},
            })
    return out


async def build_llm_messages(conv_id: str) -> list[dict]:
    from .config import settings

    rows = await get_messages(conv_id)
    msgs: list[dict] = [{"role": "system", "content": settings.system_prompt}]
    for r in rows:
        c = r["content"]
        if r["role"] == "customer":
            msgs.append({"role": "user", "content": c.get("text", "")})
        elif r["role"] == "assistant":
            if "tool_calls" in c:
                msgs.append({
                    "role": "assistant",
                    "content": c.get("text") or "",
                    "tool_calls": [
                        {
                            "id": t.get("id") or f"{r['sequence']}-{i}",
                            "type": "function",
                            "function": {
                                "name": t["name"],
                                "arguments": json.dumps(t.get("arguments") or {}, ensure_ascii=False),
                            },
                        }
                        for i, t in enumerate(c["tool_calls"])
                    ],
                })
            else:
                msgs.append({"role": "assistant", "content": c.get("text", "")})
        elif r["role"] == "tool":
            msgs.append({"role": "tool", "tool_call_id": c["tool_call_id"], "content": c["text"]})
    return msgs


async def next_action_seq(conn: Any, conv_id: str) -> int:
    row = await conn.fetchrow(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM actions WHERE conversation_id = $1",
        _uuid(conv_id),
    )
    return row["n"]


async def record(
    conn: Any,
    conv_id: str,
    actor: str,
    action: str,
    detail: dict | None = None,
    step: str | None = None,
    step_result: dict | None = None,
) -> None:
    seq = await next_action_seq(conn, conv_id)
    await conn.execute(
        """
        INSERT INTO actions (conversation_id, seq, actor, action, detail, step, step_result)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7::jsonb)
        """,
        _uuid(conv_id), seq, actor, action,
        detail or {}, step, step_result,
    )


async def get_step(conv_id: str, step_key: str) -> Any:
    return await db.fetchrow(
        "SELECT * FROM actions WHERE conversation_id = $1 AND step = $2",
        _uuid(conv_id), step_key,
    )


async def get_step_conn(conn: Any, conv_id: str, step_key: str) -> Any:
    return await conn.fetchrow(
        "SELECT * FROM actions WHERE conversation_id = $1 AND step = $2",
        _uuid(conv_id), step_key,
    )


async def list_actions(conv_id: str) -> list[Any]:
    return await db.fetch(
        "SELECT * FROM actions WHERE conversation_id = $1 ORDER BY seq",
        _uuid(conv_id),
    )


async def create_pending(
    conn: Any, conv_id: str, tool_name: str, arguments: dict, amount: float,
) -> str:
    seq = await next_action_seq(conn, conv_id)
    approval_id = f"PA-{seq}"
    await conn.execute(
        """
        INSERT INTO pending_approvals
            (id, conversation_id, tool_name, arguments, amount, sla_deadline)
        VALUES ($1, $2, $3, $4::jsonb, $5, now() + make_interval(mins => $6))
        """,
        approval_id, _uuid(conv_id), tool_name, arguments, amount,
        _sla_minutes(),
    )
    return approval_id


def _sla_minutes() -> int:
    from .config import settings

    return settings.approval_sla_minutes


async def get_pending(approval_id: str) -> Any:
    return await db.fetchrow("SELECT * FROM pending_approvals WHERE id = $1", approval_id)


async def get_pending_conn(conn: Any, approval_id: str) -> Any:
    return await conn.fetchrow("SELECT * FROM pending_approvals WHERE id = $1", approval_id)


async def get_active_pending(conv_id: str) -> Any:
    return await db.fetchrow(
        "SELECT * FROM pending_approvals WHERE conversation_id = $1 AND status = 'pending'",
        _uuid(conv_id),
    )


async def list_pendings(status: str | None = None) -> list[Any]:
    if status:
        return await db.fetch(
            "SELECT * FROM pending_approvals WHERE status = $1 ORDER BY created_at", status
        )
    return await db.fetch("SELECT * FROM pending_approvals ORDER BY created_at")


async def set_pending_status(conn: Any, approval_id: str, status: str, owner: str | None = None) -> None:
    if owner:
        await conn.execute(
            "UPDATE pending_approvals SET status = $2, owner = $3, decided_at = now() WHERE id = $1",
            approval_id, status, owner,
        )
    else:
        await conn.execute(
            "UPDATE pending_approvals SET status = $2, decided_at = now() WHERE id = $1",
            approval_id, status,
        )


async def transfer_pending_owner_to_seat(conn: Any, conv_id: str) -> None:
    """转人工时，未决待审批项随会话移交（CONTEXT.md），不作废。"""
    await conn.execute(
        "UPDATE pending_approvals SET owner = 'seat' WHERE conversation_id = $1 AND status = 'pending'",
        _uuid(conv_id),
    )


async def get_or_create_ticket(conn: Any, conv_id: str) -> Any:
    row = await conn.fetchrow(
        "SELECT * FROM tickets WHERE conversation_id = $1 ORDER BY id DESC LIMIT 1",
        _uuid(conv_id),
    )
    if row is None:
        row = await conn.fetchrow(
            "INSERT INTO tickets (conversation_id) VALUES ($1) RETURNING *", _uuid(conv_id)
        )
    return row


async def set_ticket_status(conn: Any, conv_id: str, status: str) -> None:
    await conn.execute(
        "UPDATE tickets SET status = $2, updated_at = now() WHERE conversation_id = $1",
        _uuid(conv_id), status,
    )


async def get_quota_conn(conn: Any, action_type: str) -> float:
    from .config import settings

    row = await conn.fetchrow("SELECT amount_limit FROM quotas WHERE action_type = $1", action_type)
    return float(row["amount_limit"]) if row else settings.default_refund_quota
