"""SSE 检查点：每回合一个 Redis Stream（消息级断线续传，研究笔记 §3.1）。

回合写入方把每个 delta 检查点到 Stream；客户端断线后带 Last-Event-ID
重连 GET /stream，从检查点重放缺失事件并跟随到 done。Redis 不可用时
检查点静默跳过——断线重连退化为从 messages 端点拉全量历史（现状行为）。
"""

import asyncio
import json

import redis.asyncio as aioredis

from .config import settings

_pool: aioredis.Redis | None = None

TERMINAL_TYPES = ("done", "error")


async def connect() -> None:
    global _pool
    _pool = aioredis.from_url(settings.redis_url, decode_responses=True)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


def _key(conv_id: str) -> str:
    return f"turn:{conv_id}"


async def start_turn(conv_id: str) -> None:
    """新回合清空该会话的检查点流（同一时刻至多一个未完成回合，ADR-0002）。"""
    if _pool is None:
        return
    try:
        await _pool.delete(_key(conv_id))
    except Exception:
        pass


async def add_event(conv_id: str, payload: dict) -> str:
    """检查点写入，返回 Stream 事件 id（作 SSE id）；Redis 不可用返回空串。"""
    if _pool is None:
        return ""
    try:
        entry_id = await _pool.xadd(
            _key(conv_id), {"payload": json.dumps(payload, ensure_ascii=False)}
        )
        return str(entry_id)
    except Exception:
        return ""


async def replay(
    conv_id: str,
    last_id: str = "0",
    idle_timeout_seconds: float = 5.0,
):
    """从 last_id 之后重放并跟随，直到 done/error；无终结事件则超时放弃。"""
    if _pool is None:
        return
    cursor = last_id
    deadline = asyncio.get_running_loop().time() + idle_timeout_seconds
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        try:
            resp = await _pool.xread({_key(conv_id): cursor}, block=int(remaining * 1000), count=100)
        except Exception:
            return
        if not resp:
            return
        for _, entries in resp:
            for entry_id, fields in entries:
                cursor = entry_id
                payload = json.loads(fields["payload"])
                yield {"id": entry_id, "payload": payload}
                if payload.get("type") in TERMINAL_TYPES:
                    return
