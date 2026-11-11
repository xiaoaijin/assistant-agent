"""坐席工作台 seam：会话列表端点（GET /api/conversations）。

列表按最近消息时间倒序（无消息用 created_at），每项带未决待审批项摘要，
供工作台一眼看到哪些会话挂着审批。
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from app import db, store
from app.main import app


@pytest_asyncio.fixture
async def client(db_pool):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c


@pytest.fixture
async def shop_id(db_pool):
    row = await db_pool.fetchrow("INSERT INTO shops (name) VALUES ('测试店铺') RETURNING id")
    return str(row["id"])


async def _mk_conv(shop_id: str, *, customer: str, created_at=None, status="ai_serving") -> str:
    conv = await store.create_conversation(customer, shop_id)
    conv_id = str(conv["id"])
    await db.pool().execute(
        "UPDATE conversations SET created_at = COALESCE($2, created_at), status = $3 WHERE id = $1",
        db.uuid(conv_id), created_at, status,
    )
    return conv_id


async def test_empty_list_returns_no_conversations(client):
    assert (await client.get("/api/conversations")).json() == []


async def test_list_orders_by_last_message_desc(client, shop_id):
    older = await _mk_conv(shop_id, customer="c1", created_at=datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc))
    newer = await _mk_conv(shop_id, customer="c2", created_at=datetime(2026, 11, 11, 10, 0, tzinfo=timezone.utc))
    await store.add_message(db.pool(), older, "customer", {"text": "更早的消息"})
    await db.pool().execute(
        "UPDATE messages SET created_at = $2 WHERE conversation_id = $1",
        db.uuid(older), datetime(2026, 11, 11, 11, 0, tzinfo=timezone.utc),
    )

    rows = (await client.get("/api/conversations")).json()
    assert [r["id"] for r in rows] == [older, newer]  # 有最近消息的排在无消息的之前


async def test_list_filters_by_status(client, shop_id):
    await _mk_conv(shop_id, customer="c1", status="ai_serving")
    transferred = await _mk_conv(shop_id, customer="c2", status="transferred")

    rows = (await client.get("/api/conversations", params={"status": "transferred"})).json()
    assert [r["id"] for r in rows] == [transferred]
    assert all(r["status"] == "transferred" for r in rows)


async def test_list_includes_pending_approval_summary(client, shop_id):
    _NOW = datetime.now(timezone.utc)
    conv_id = await _mk_conv(shop_id, customer="c1")
    other = await _mk_conv(shop_id, customer="c2")
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_approvals (id, conversation_id, tool_name, arguments, amount, sla_deadline)
            VALUES ('PA-1', $1, 'create_refund', '{}', 500, $2)
            """,
            db.uuid(conv_id), _NOW + timedelta(minutes=30),
        )

    rows = (await client.get("/api/conversations")).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[conv_id]["pending_approval"]["id"] == "PA-1"
    assert by_id[conv_id]["pending_approval"]["amount"] == 500
    assert by_id[conv_id]["pending_approval"]["escalated"] is False
    assert by_id[conv_id]["pending_approval"]["sla_deadline"] is not None
    assert by_id[other]["pending_approval"] is None
