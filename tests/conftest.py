"""集成测试基建：pgvector 测试库（docker compose db，端口 5433），不可用则 skip。

每个测试从空库出发：truncate 全部业务表。
"""

import socket

import pytest
import pytest_asyncio

from app import db

_TABLES = [
    "knowledge_chunks", "knowledge_documents", "messages", "actions",
    "pending_approvals", "tickets", "conversations", "admin_users", "shops",
]


def _db_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 5433), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def _db_guard():
    if not _db_reachable():
        pytest.skip("测试库不可用：先 docker compose up -d db")


@pytest_asyncio.fixture
async def db_pool(_db_guard):
    await db.init_pool()
    async with db.pool().acquire() as conn:
        await conn.execute(f"TRUNCATE {', '.join(_TABLES)} CASCADE")
    yield db.pool()
    await db.close_pool()
