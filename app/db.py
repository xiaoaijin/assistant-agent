"""Postgres 连接池与 schema。

单库承载关系数据与（将来的）向量（ADR-0004）。所有状态落库：
进程可死可生，重启后从 DB 重建循环上下文（ADR-0002）。
"""

import json
from typing import Any

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

-- 店铺（CONTEXT.md 知识库按店铺隔离；ADR-0005）
CREATE TABLE IF NOT EXISTS shops (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name     TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 管理端账号：shop_id 为空即平台超管，可跨店（ADR-0005）
CREATE TABLE IF NOT EXISTS admin_users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    shop_id       UUID REFERENCES shops(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id TEXT NOT NULL,
    shop_id     UUID NOT NULL REFERENCES shops(id),
    channel     TEXT NOT NULL DEFAULT 'web',
    status      TEXT NOT NULL DEFAULT 'ai_serving',  -- ai_serving | transferred
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 消息（sequence 唯一：幂等写入 + 顺序保证，参考 AutoGPT ChatMessage）
CREATE TABLE IF NOT EXISTS messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    sequence        INT  NOT NULL,
    role            TEXT NOT NULL,          -- customer | assistant | tool
    content         JSONB NOT NULL,         -- {text} | {tool_calls} | {tool_call_id, text}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, sequence)
);

-- 动作留痕（CONTEXT.md「动作留痕」）：每一次动作可追溯；
-- step 非空的行同时是循环步骤账本 —— 恰好一次语义的落点（ADR-0002）
CREATE TABLE IF NOT EXISTS actions (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    seq             INT  NOT NULL,
    actor           TEXT NOT NULL,          -- customer | assistant | system | seat
    action          TEXT NOT NULL,          -- ask | tool:get_order | reply | quota_check | approve | ...
    detail          JSONB NOT NULL DEFAULT '{}',
    step            TEXT,                   -- 幂等键，如 tool:{msg_seq}:{idx} / execute:{approval_id}
    step_result     JSONB,                  -- 步骤持久化结果：重启后读这里，不重放执行
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_actions_step
    ON actions (conversation_id, step) WHERE step IS NOT NULL;

-- 待审批项（CONTEXT.md「待审批项」：转人工时随会话移交，不作废）
CREATE TABLE IF NOT EXISTS pending_approvals (
    id              TEXT PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    tool_name       TEXT NOT NULL,
    arguments       JSONB NOT NULL,
    amount          NUMERIC(12,2),
    status          TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | executed
    owner           TEXT NOT NULL DEFAULT 'conversation',  -- conversation | seat
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS tickets (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    status          TEXT NOT NULL DEFAULT 'processing',  -- processing | waiting | resolved | closed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 规则额度制（CONTEXT.md「额度」）
CREATE TABLE IF NOT EXISTS quotas (
    action_type  TEXT PRIMARY KEY,
    amount_limit NUMERIC(12,2) NOT NULL
);
INSERT INTO quotas (action_type, amount_limit)
VALUES ('refund', 100)
ON CONFLICT (action_type) DO NOTHING;

-- 知识文档与切片（ADR-0005）：原文必须落库（管理页展示/编辑、换模型重切片重灌）
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id      UUID NOT NULL REFERENCES shops(id),
    content_type TEXT NOT NULL,               -- faq | policy | ticket | ...
    title        TEXT NOT NULL,
    source_key   TEXT NOT NULL,               -- 幂等 upsert 键（同 key 重传 = 更新）
    raw          BYTEA NOT NULL,              -- 原始文件字节（重切片依据）
    filename     TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active',
                 -- active | pending_review | rejected | superseded
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (shop_id, source_key)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    seq             INT  NOT NULL,
    text            TEXT NOT NULL,
    title_path      TEXT NOT NULL DEFAULT '',
    page            INT,
    embedding       vector(1024) NOT NULL,
    char_count      INT  NOT NULL,
    chunker_version INT  NOT NULL,
    UNIQUE (document_id, seq)
);

-- 检索索引：余弦距离（pgvector 原生 ivfflat/hnsw 由 migration 决定，先用顺序扫描+索引可选）
CREATE INDEX IF NOT EXISTS ix_chunks_document ON knowledge_chunks (document_id);
"""


async def _init_connection(conn: asyncpg.Connection) -> None:
    """JSONB 编解码：进出都是 Python dict（asyncpg 默认返回字符串）。"""
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, ensure_ascii=False),
        decoder=json.loads,
        schema="pg_catalog",
    )


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, init=_init_connection)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool not initialised"
    return _pool


def j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def uuid(v: Any) -> Any:
    if v is None:
        return None
    import uuid as _uuid

    return v if isinstance(v, _uuid.UUID) else _uuid.UUID(str(v))


async def fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    async with pool().acquire() as conn:
        return await conn.fetch(sql, *args)


async def fetchrow(sql: str, *args: Any) -> asyncpg.Record | None:
    async with pool().acquire() as conn:
        return await conn.fetchrow(sql, *args)
