"""摄取管线：文档字节 → 切片 → 嵌入 → 落库（同步），含冲突检测（ADR-0005）。

同 source_key 重传 = 更新；不同 key 走冲突三档：
- 全部 chunk 判重 → 文档置 duplicate（不入库切片）
- 任一 chunk 疑似冲突 → 文档置 pending_review（切片照存，检索只查 active）
- 否则 → active
"""

from typing import Any, Protocol

from .. import db
from . import chunker, conflict
from .store import (
    best_similarity_active,
    delete_chunks,
    insert_chunk,
    upsert_document,
)


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


async def ingest_document(
    shop_id: str,
    content_type: str,
    source_key: str,
    title: str,
    filename: str,
    data: bytes,
    created_by: str,
    embedder: Embedder,
) -> dict:
    chunks = chunker.chunk_document(content_type, chunker.parse_document(filename, data))
    if not chunks:
        raise ValueError("文档没有可提取的内容")
    vectors = await embedder.embed([c.text for c in chunks])

    stored, skipped, has_conflict = 0, 0, False
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            doc = await upsert_document(
                conn, shop_id, content_type, title, source_key, data, filename, created_by,
            )
            await delete_chunks(conn, str(doc["id"]))
            for chunk, vec in zip(chunks, vectors):
                verdict = conflict.classify_chunk_conflict(
                    await best_similarity_active(conn, shop_id, source_key, vec)
                )
                if verdict == "duplicate":
                    skipped += 1
                    continue
                if verdict == "conflict":
                    has_conflict = True
                await insert_chunk(
                    conn, str(doc["id"]), chunk.seq, chunk.text, chunk.title_path,
                    chunk.meta.get("page"), vec, chunk.char_count,
                    chunker.CHUNKER_VERSION,
                )
                stored += 1
            status = (
                "duplicate" if stored == 0
                else "pending_review" if has_conflict
                else "active"
            )
            if status != "active":
                await conn.execute(
                    "UPDATE knowledge_documents SET status = $2 WHERE id = $1",
                    doc["id"], status,
                )
    return {"status": status, "document_id": str(doc["id"]), "chunks_stored": stored, "chunks_skipped": skipped}
