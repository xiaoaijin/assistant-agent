"""定时任务 seam：催付/回访提醒（SQL jobstore 持久化 + 到点执行）与 SLA 定期清扫。

job 执行函数直接调用，不真等触发时间；持久化用 BackgroundScheduler 验证重启可恢复。
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio

from app import db, scheduler, store
from app.config import settings
from app.main import app


@pytest_asyncio.fixture
async def client(db_pool):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)

REMINDER_TEXTS = {
    "催付": "您的订单还有待付款项，请尽快完成支付，有问题随时找我。",
    "回访": "您好，请问这次的购物体验还满意吗？欢迎随时反馈。",
}


@pytest.fixture
async def conv_id(db_pool):
    shop = await db_pool.fetchrow("INSERT INTO shops (name) VALUES ('测试店铺') RETURNING id")
    conv = await store.create_conversation("c1", str(shop["id"]))
    return str(conv["id"])


async def test_run_reminder_posts_assistant_message_and_trace(conv_id):
    job_id = "reminder:c1:催付:20261111"
    await scheduler.run_reminder(conv_id, "催付", job_id)

    messages = await store.get_messages(conv_id)
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"]["text"] == REMINDER_TEXTS["催付"]

    trace = await db.pool().fetch(
        "SELECT actor, action, step FROM actions WHERE conversation_id = $1 AND action = 'reminder'",
        db.uuid(conv_id),
    )
    assert len(trace) == 1
    assert trace[0]["actor"] == "system"
    assert trace[0]["step"] == job_id


async def test_run_reminder_is_idempotent(conv_id):
    job_id = "reminder:c1:回访:20261111"
    await scheduler.run_reminder(conv_id, "回访", job_id)
    await scheduler.run_reminder(conv_id, "回访", job_id)  # 重放不重复发送

    messages = await store.get_messages(conv_id)
    assert sum(1 for m in messages if m["role"] == "assistant") == 1


async def test_unknown_kind_is_rejected(conv_id):
    with pytest.raises(ValueError):
        await scheduler.run_reminder(conv_id, "骚扰", "reminder:x")


async def test_reminder_job_survives_scheduler_restart():
    run_at = _NOW + timedelta(hours=1)

    s1 = scheduler.create_background_scheduler(settings.database_url)
    s1.start()
    s1.remove_all_jobs()  # jobstore 跨测试共享，先清空
    job_id = scheduler.schedule_reminder(s1, "conv-x", "催付", run_at)
    s1.shutdown()

    s2 = scheduler.create_background_scheduler(settings.database_url)
    s2.start()
    jobs = s2.get_jobs()
    s2.shutdown()
    assert [j.id for j in jobs] == [job_id]
    assert list(jobs[0].args) == ["conv-x", "催付", job_id]  # jobstore 往返后 args 成 tuple


async def test_sla_sweep_job_escalates_overdue(db_pool, conv_id):
    deadline = _NOW - timedelta(minutes=5)
    async with db.pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pending_approvals (id, conversation_id, tool_name, arguments, amount, sla_deadline)
            VALUES ('PA-1', $1, 'create_refund', '{}', 500, $2)
            """,
            db.uuid(conv_id), deadline,
        )
    escalated = await scheduler.run_sla_sweep(_NOW)
    assert escalated == ["PA-1"]


async def test_reminder_endpoint_schedules_job(client, conv_id, monkeypatch):
    bg = scheduler.create_background_scheduler(settings.database_url)
    bg.start()
    monkeypatch.setattr(scheduler, "_async_scheduler", bg)
    try:
        resp = await client.post(
            f"/api/conversations/{conv_id}/reminders",
            json={"kind": "催付", "run_at": (_NOW + timedelta(hours=1)).isoformat()},
        )
        assert resp.status_code == 200
        job_id = resp.json()["job_id"]
        assert job_id.startswith(f"reminder:{conv_id}:催付:")

        bad = await client.post(
            f"/api/conversations/{conv_id}/reminders",
            json={"kind": "骚扰", "run_at": (_NOW + timedelta(hours=1)).isoformat()},
        )
        assert bad.status_code == 422
    finally:
        bg.shutdown()
