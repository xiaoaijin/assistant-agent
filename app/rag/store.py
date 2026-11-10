"""知识库数据访问：店铺、文档、切片（向量以文本形式传给 pgvector，避免类型适配依赖）。"""

from typing import Any

from .. import db


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(repr(v) for v in vec) + "]"


async def create_shop(name: str) -> Any:
    return await db.fetchrow("INSERT INTO shops (name) VALUES ($1) RETURNING *", name)


async def upsert_document(
    conn: Any,
    shop_id: str,
    content_type: str,
    title: str,
    source_key: str,
    raw: bytes,
    filename: str,
    created_by: str,
) -> Any:
    """同 (shop_id, source_key) 重传 = 更新（原文与元信息替换，切片由调用方先删后插）。"""
    return await conn.fetchrow(
        """
        INSERT INTO knowledge_documents
            (shop_id, content_type, title, source_key, raw, filename, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (shop_id, source_key) DO UPDATE SET
            content_type = EXCLUDED.content_type,
            title = EXCLUDED.title,
            raw = EXCLUDED.raw,
            filename = EXCLUDED.filename,
            status = 'active',
            updated_at = now()
        RETURNING *
        """,
        db.uuid(shop_id), content_type, title, source_key, raw, filename, created_by,
    )


async def set_document_status(conn: Any, document_id: str, status: str) -> None:
    await conn.execute(
        "UPDATE knowledge_documents SET status = $2, updated_at = now() WHERE id = $1",
        db.uuid(document_id), status,
    )


async def delete_chunks(conn: Any, document_id: str) -> None:
    await conn.execute("DELETE FROM knowledge_chunks WHERE document_id = $1", db.uuid(document_id))


async def insert_chunk(
    conn: Any,
    document_id: str,
    seq: int,
    text: str,
    title_path: str,
    page: int | None,
    embedding: list[float],
    char_count: int,
    chunker_version: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO knowledge_chunks
            (document_id, seq, text, title_path, page, embedding, char_count, chunker_version)
        VALUES ($1, $2, $3, $4, $5, $6::vector, $7, $8)
        """,
        db.uuid(document_id), seq, text, title_path, page,
        to_pgvector(embedding), char_count, chunker_version,
    )


async def best_similarity_active(
    conn: Any, shop_id: str, exclude_source_key: str, embedding: list[float],
) -> float | None:
    """该店铺 active 文档切片中与向量的最高余弦相似度；无近邻返回 None。"""
    row = await conn.fetchrow(
        """
        SELECT MAX(1 - (kc.embedding <=> $1::vector)) AS score
        FROM knowledge_chunks kc
        JOIN knowledge_documents kd ON kd.id = kc.document_id
        WHERE kd.shop_id = $2 AND kd.status = 'active' AND kd.source_key <> $3
        """,
        to_pgvector(embedding), db.uuid(shop_id), exclude_source_key,
    )
    return float(row["score"]) if row and row["score"] is not None else None


async def get_document(document_id: str) -> Any:
    return await db.fetchrow("SELECT * FROM knowledge_documents WHERE id = $1", db.uuid(document_id))


async def list_documents(shop_id: str | None = None, status: str | None = None) -> list[Any]:
    sql = "SELECT * FROM knowledge_documents WHERE ($1::uuid IS NULL OR shop_id = $1::uuid)"
    sql += " AND ($2::text IS NULL OR status = $2::text) ORDER BY updated_at DESC"
    return await db.fetch(sql, db.uuid(shop_id) if shop_id else None, status)


async def list_chunks(document_id: str) -> list[Any]:
    return await db.fetch(
        "SELECT seq, text, title_path, page, char_count FROM knowledge_chunks"
        " WHERE document_id = $1 ORDER BY seq",
        db.uuid(document_id),
    )
