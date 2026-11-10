"""语义检索：pgvector 余弦 top-k，只查 active 文档、限定店铺（ADR-0005）。"""

from typing import Any, Protocol

from .. import db
from ..config import settings
from .store import to_pgvector


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


async def semantic_search(
    shop_id: str,
    query: str,
    embedder: Embedder,
    k: int | None = None,
    min_score: float | None = None,
) -> list[dict]:
    top_k = k if k is not None else settings.search_top_k
    threshold = min_score if min_score is not None else settings.search_min_score
    [query_vec] = await embedder.embed([query])
    rows = await db.fetch(
        """
        SELECT kc.seq, kc.text, kc.title_path, kc.page, kc.document_id,
               1 - (kc.embedding <=> $1::vector) AS score
        FROM knowledge_chunks kc
        JOIN knowledge_documents kd ON kd.id = kc.document_id
        WHERE kd.shop_id = $2 AND kd.status = 'active'
        ORDER BY kc.embedding <=> $1::vector
        LIMIT $3
        """,
        to_pgvector(query_vec), db.uuid(shop_id), top_k,
    )
    results = [
        {
            "seq": r["seq"], "text": r["text"], "title_path": r["title_path"],
            "page": r["page"], "document_id": str(r["document_id"]),
            "score": float(r["score"]),
        }
        for r in rows
    ]
    if threshold is not None:
        results = [r for r in results if r["score"] >= threshold]
    return results
