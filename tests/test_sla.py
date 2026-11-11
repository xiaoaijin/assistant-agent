"""S2 清扫器 seam：超 SLA 待审批项升级（标记 + 动作留痕，幂等）。

升级语义：不自动批准/拒绝，坐席仍是决策人；只标记 escalated_at 并留痕。
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from app import approvals, db, store
from app.config import settings
from app.main import app

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def client(db_pool):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c


@pytest.fixture
async def conv_id(db_pool):
    shop = await db_pool.fetchrow(
        "INSERT INTO shops (name) VALUES ('测试店铺') RETURNING id"
    )
    conv = await store.create_conversation("c1", str(shop["id"]))
    return str(conv["id"])


async def _mk_pending(conn, conv_id: str, *, deadline, status="pending", seq=1) -> str:
    approval_id = f"PA-{seq}"
    await conn.execute(
        """
        INSERT INTO pending_approvals (id, conversation_id, tool_name, arguments, amount, sla_deadline, status)
        VALUES ($1, $2, 'create_refund', '{}', 500, $3, $4)
        """,
        approval_id, db.uuid(conv_id), deadline, status,
    )
    return approval_id


async def test_before_deadline_sweep_escalates_nothing(conv_id):
    async with db.pool().acquire() as conn:
        await _mk_pending(conn, conv_id, deadline=_NOW + timedelta(minutes=10), seq=1)
    assert await approvals.sweep_overdue(_NOW) == []


async def test_past_deadline_pending_is_escalated_and_traced(conv_id):
    async with db.pool().acquire() as conn:
        approval_id = await _mk_pending(
            conn, conv_id, deadline=_NOW - timedelta(minutes=5), seq=1
        )
    escalated = await approvals.sweep_overdue(_NOW)
    assert escalated == [approval_id]

    async with db.pool().acquire() as conn:
        p = await store.get_pending_conn(conn, approval_id)
        assert p["status"] == "pending"  # 坐席仍是决策人
        assert p["escalated_at"] is not None
        trace = await db.pool().fetch(
            "SELECT actor, action, detail FROM actions "
            "WHERE conversation_id = $1 AND action = 'sla_escalate'",
            db.uuid(conv_id),
        )
        assert len(trace) == 1
        assert trace[0]["actor"] == "system"
        assert trace[0]["detail"]["approval_id"] == approval_id


async def test_sweep_is_idempotent(conv_id):
    async with db.pool().acquire() as conn:
        approval_id = await _mk_pending(
            conn, conv_id, deadline=_NOW - timedelta(minutes=5), seq=1
        )
    assert await approvals.sweep_overdue(_NOW) == [approval_id]
    assert await approvals.sweep_overdue(_NOW) == []
    trace = await db.pool().fetch(
        "SELECT 1 FROM actions WHERE conversation_id = $1 AND action = 'sla_escalate'",
        db.uuid(conv_id),
    )
    assert len(trace) == 1


async def test_decided_or_undated_pendings_are_skipped(conv_id):
    async with db.pool().acquire() as conn:
        await _mk_pending(
            conn, conv_id, deadline=_NOW - timedelta(minutes=5),
            status="executed", seq=1,
        )
        await _mk_pending(conn, conv_id, deadline=None, seq=2)
    assert await approvals.sweep_overdue(_NOW) == []


async def test_create_pending_sets_sla_deadline(conv_id):
    async with db.pool().acquire() as conn:
        approval_id = await store.create_pending(conn, conv_id, "create_refund", {}, 500.0)
        p = await store.get_pending_conn(conn, approval_id)
    assert p["sla_deadline"] is not None
    remaining = p["sla_deadline"] - datetime.now(timezone.utc)
    assert timedelta(0) < remaining <= timedelta(minutes=settings.approval_sla_minutes)


async def test_list_approvals_exposes_sla_fields(client, conv_id):
    async with db.pool().acquire() as conn:
        await _mk_pending(conn, conv_id, deadline=_NOW + timedelta(minutes=10), seq=1)
    rows = (await client.get("/api/approvals")).json()
    assert len(rows) == 1
    assert rows[0]["sla_deadline"] is not None
    assert rows[0]["escalated"] is False


async def test_sweep_endpoint_escalates_and_is_idempotent(client, conv_id):
    # 端点内部用真实时钟，deadline 相对真实 now 构造
    deadline = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with db.pool().acquire() as conn:
        approval_id = await _mk_pending(conn, conv_id, deadline=deadline, seq=1)
    first = (await client.post("/api/approvals/sweep")).json()
    assert first == [approval_id]
    assert (await client.post("/api/approvals/sweep")).json() == []

    rows = (await client.get("/api/approvals")).json()
    assert rows[0]["escalated"] is True
