"""召回评估（S6）：合成知识库 + 真实嵌入 → recall@5。

需要 docker compose db（pgvector）与 DASHSCOPE_API_KEY，缺任一则 skip。
阈值默认 0.8，可用 RECALL_MIN 环境变量覆盖。跑分：pytest tests/eval -s
"""

import os
import pathlib
import socket

import pytest
import yaml

from app import db
from app.rag.embeddings import LiteLLMEmbedder
from app.rag.ingest import ingest_document
from app.rag.search import semantic_search
from app.rag.store import create_shop

EVAL_DIR = pathlib.Path(__file__).parent


def _db_reachable() -> bool:
    try:
        with socket.create_connection(("localhost", 5433), timeout=1):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.skipif(not _db_reachable(), reason="测试库不可用：先 docker compose up -d db"),
    pytest.mark.skipif(
        not os.getenv("DASHSCOPE_API_KEY"), reason="缺 DASHSCOPE_API_KEY：召回评估需要真实嵌入"
    ),
]


@pytest.fixture
async def eval_shop(db_pool):
    shop = await create_shop("召回评估")
    shop_id = str(shop["id"])
    doc_ids: dict[str, str] = {}
    for path in sorted((EVAL_DIR / "knowledge").iterdir()):
        result = await ingest_document(
            shop_id=shop_id,
            content_type="faq" if path.suffix in (".yaml", ".yml") else "policy",
            source_key=path.stem,
            title=path.stem,
            filename=path.name,
            data=path.read_bytes(),
            created_by="eval",
            embedder=LiteLLMEmbedder(),
        )
        doc_ids[path.stem] = result["document_id"]
    yield shop_id, doc_ids
    async with db.pool().acquire() as conn:
        await conn.execute("DELETE FROM knowledge_documents WHERE shop_id = $1", shop["id"])
        await conn.execute("DELETE FROM shops WHERE id = $1", shop["id"])


async def test_recall_at_5(eval_shop):
    shop_id, doc_ids = eval_shop
    cases = yaml.safe_load((EVAL_DIR / "recall_cases.yaml").read_text())["cases"]

    hits, misses = 0, []
    for case in cases:
        results = await semantic_search(shop_id, case["query"], LiteLLMEmbedder())
        got = {r["document_id"] for r in results[:5]}
        if doc_ids[case["expect_source_key"]] in got:
            hits += 1
        else:
            top1 = results[0] if results else None
            misses.append(
                f"  [{case['expect_source_key']}] {case['query']}"
                + (f" → 命中: {top1['title_path']} ({top1['score']:.3f})" if top1 else " → 无结果")
            )

    recall = hits / len(cases)
    min_recall = float(os.getenv("RECALL_MIN", "0.8"))
    print(f"\nrecall@5 = {recall:.2%} ({hits}/{len(cases)})，阈值 {min_recall:.0%}")
    if misses:
        print("未命中用例:")
        print("\n".join(misses))
    assert recall >= min_recall, f"recall@5 {recall:.2%} 低于阈值 {min_recall:.0%}，未命中:\n" + "\n".join(misses)
