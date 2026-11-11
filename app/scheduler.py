"""定时任务：SLA 定期清扫 + 催付/回访提醒（APScheduler + SQL jobstore）。

job 持久化在同一个 Postgres（ADR-0004），重启后自动恢复；错过触发
（misfire）在宽限期内补跑一次。提醒执行用 actions.step 幂等：重放不重复发送。
"""

from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler

from . import db, store

REMINDER_TEXTS = {
    "催付": "您的订单还有待付款项，请尽快完成支付，有问题随时找我。",
    "回访": "您好，请问这次的购物体验还满意吗？欢迎随时反馈。",
}

SWEEP_INTERVAL_SECONDS = 60
REMINDER_MISFIRE_GRACE_SECONDS = 3600

_async_scheduler: AsyncIOScheduler | None = None


def _jobstore(database_url: str) -> SQLAlchemyJobStore:
    # SQLAlchemy 2.1 起默认驱动是 psycopg3，这里显式用 psycopg2
    url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return SQLAlchemyJobStore(url=url)


def create_background_scheduler(database_url: str) -> BackgroundScheduler:
    return BackgroundScheduler(jobstores={"default": _jobstore(database_url)})


def create_async_scheduler(database_url: str) -> AsyncIOScheduler:
    return AsyncIOScheduler(jobstores={"default": _jobstore(database_url)})


def schedule_reminder(sched, conv_id: str, kind: str, run_at: datetime) -> str:
    if kind not in REMINDER_TEXTS:
        raise ValueError(f"未知提醒类型：{kind}")
    run_at_utc = run_at.astimezone(timezone.utc)
    job_id = f"reminder:{conv_id}:{kind}:{run_at_utc.strftime('%Y%m%d%H%M%S')}"
    sched.add_job(
        run_reminder,
        "date",
        run_date=run_at_utc,
        id=job_id,
        args=[conv_id, kind, job_id],
        misfire_grace_time=REMINDER_MISFIRE_GRACE_SECONDS,
        replace_existing=True,
    )
    return job_id


async def run_reminder(conv_id: str, kind: str, job_id: str) -> None:
    if kind not in REMINDER_TEXTS:
        raise ValueError(f"未知提醒类型：{kind}")
    text = REMINDER_TEXTS[kind]
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            existing = await store.get_step_conn(conn, conv_id, job_id)
            if existing is not None:
                return
            await store.add_message(conn, conv_id, "assistant", {"text": text})
            await store.record(
                conn, conv_id, "system", "reminder",
                {"kind": kind, "job_id": job_id},
                step=job_id, step_result={"text": text},
            )


async def run_sla_sweep(now: datetime | None = None) -> list[str]:
    from . import approvals

    return await approvals.sweep_overdue(now or datetime.now(timezone.utc))


def start(database_url: str) -> AsyncIOScheduler:
    global _async_scheduler
    sched = create_async_scheduler(database_url)
    sched.add_job(
        run_sla_sweep,
        "interval",
        seconds=SWEEP_INTERVAL_SECONDS,
        id="sla_sweep",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=SWEEP_INTERVAL_SECONDS),
    )
    sched.start()
    _async_scheduler = sched
    return sched


def current() -> AsyncIOScheduler:
    assert _async_scheduler is not None, "调度器未启动"
    return _async_scheduler


def shutdown() -> None:
    global _async_scheduler
    if _async_scheduler is not None:
        _async_scheduler.shutdown(wait=False)
        _async_scheduler = None
