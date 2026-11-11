"""消息级断线续传 seam：SSE delta 检查点到 Redis Stream，Last-Event-ID 重连重放。

回合写入方（POST /messages 的 run 任务）调用 stream.add_event；
重连方（GET /stream，带 Last-Event-ID）从检查点重放缺失事件并跟随到 done。
Redis 不可用则 skip（docker compose up -d redis）。
"""

import socket

import httpx
import pytest
import pytest_asyncio

from app import store, stream
from app.main import app


def _redis_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 6379), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
def _redis_guard():
    if not _redis_reachable():
        pytest.skip("Redis 不可用：先 docker compose up -d redis")


@pytest_asyncio.fixture
async def redis_conn(_redis_guard):
    await stream.connect()
    yield
    await stream.close()


@pytest_asyncio.fixture
async def client(db_pool, redis_conn):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as c:
        yield c


@pytest.fixture
async def conv_id(db_pool):
    shop = await db_pool.fetchrow("INSERT INTO shops (name) VALUES ('测试店铺') RETURNING id")
    conv = await store.create_conversation("c1", str(shop["id"]))
    return str(conv["id"])


async def test_add_and_replay_roundtrip(conv_id, redis_conn):
    await stream.start_turn(conv_id)
    id1 = await stream.add_event(conv_id, {"type": "delta", "text": "您"})
    id2 = await stream.add_event(conv_id, {"type": "delta", "text": "好"})
    id3 = await stream.add_event(conv_id, {"type": "done", "text": ""})

    all_events = [e async for e in stream.replay(conv_id, "0")]
    assert [e["id"] for e in all_events] == [id1, id2, id3]

    from_mid = [e async for e in stream.replay(conv_id, id1)]
    assert [e["payload"]["text"] for e in from_mid] == ["好", ""]  # id1 之后：好 + done
    assert from_mid[-1]["payload"]["type"] == "done"  # 跟随到 done 才结束


async def test_replay_times_out_without_done(conv_id, redis_conn):
    await stream.start_turn(conv_id)
    await stream.add_event(conv_id, {"type": "delta", "text": "孤立片段"})

    events = [e async for e in stream.replay(conv_id, "0", idle_timeout_seconds=0.2)]
    assert len(events) == 1  # 没有 done：重放已有事件后超时放弃，不悬挂


async def test_stream_endpoint_replays_from_last_event_id(client, conv_id):
    await stream.start_turn(conv_id)
    id1 = await stream.add_event(conv_id, {"type": "delta", "text": "您"})
    await stream.add_event(conv_id, {"type": "delta", "text": "好"})
    await stream.add_event(conv_id, {"type": "done", "text": ""})

    full = await client.get(f"/api/conversations/{conv_id}/stream")
    assert "您" in full.text and "好" in full.text and "done" in full.text
    assert f"id: {id1}" in full.text  # SSE event id 可作为 Last-Event-ID

    resumed = await client.get(
        f"/api/conversations/{conv_id}/stream",
        headers={"Last-Event-ID": id1},
    )
    assert "您" not in resumed.text
    assert "好" in resumed.text and "done" in resumed.text
