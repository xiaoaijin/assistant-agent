"""S3 检索 seam：semantic_search（top-k/店隔离/只查 active/阈值）+ search_knowledge 工具接入 loop。"""

from app import db, loop, store
from app.rag.ingest import ingest_document
from app.rag.search import semantic_search
from app.rag.store import create_shop
from tests.fake_embedder import FakeEmbedder, cos_pair, unit


def _md(title: str, body: str) -> bytes:
    return f"# {title}\n\n{body}\n".encode()


async def test_ranking_is_descending_and_filtered_by_shop_and_active(db_pool):
    shop = await create_shop("S1")
    other = await create_shop("S2")
    embedder = FakeEmbedder({
        "七天政策\n七天无理由退货适用于所有自营商品。": unit(0),
        "三十天政策\n退货时限为三十天。": unit(1),
        "别家政策\n别家的知识": unit(2),
        "七天无理由怎么退？": cos_pair(unit(0), unit(1), 0.8),  # 更靠近七天
    })
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="seven",
        title="七天政策", filename="seven.md", data=_md("七天政策", "七天无理由退货适用于所有自营商品。"),
        created_by="a", embedder=FakeEmbedder({"七天政策\n七天无理由退货适用于所有自营商品。": unit(0)}),
    )
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="thirty",
        title="三十天政策", filename="thirty.md", data=_md("三十天政策", "退货时限为三十天。"),
        created_by="a", embedder=FakeEmbedder({"三十天政策\n退货时限为三十天。": unit(1)}),
    )
    await ingest_document(
        shop_id=str(other["id"]), content_type="policy", source_key="other",
        title="别家政策", filename="other.md", data=_md("别家政策", "别家的知识"),
        created_by="a", embedder=FakeEmbedder({"别家政策\n别家的知识": unit(2)}),
    )
    query = "七天无理由怎么退？"
    results = await semantic_search(str(shop["id"]), query, embedder)
    assert len(results) == 2  # 别店的不会出现
    assert results[0]["title_path"] == "七天政策"
    assert results[0]["score"] >= results[1]["score"]
    assert results[0]["text"].startswith("七天政策\n")


async def test_min_score_filters_low_similarity(db_pool):
    shop = await create_shop("S1")
    embedder = FakeEmbedder({
        "七天政策\n七天无理由退货适用于所有自营商品。": unit(0),
        "完全无关的问题": unit(9),
    })
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="seven",
        title="七天政策", filename="seven.md", data=_md("七天政策", "七天无理由退货适用于所有自营商品。"),
        created_by="a", embedder=FakeEmbedder({"七天政策\n七天无理由退货适用于所有自营商品。": unit(0)}),
    )
    results = await semantic_search(
        str(shop["id"]), "完全无关的问题", embedder, min_score=0.5,
    )
    assert results == []


async def test_search_knowledge_tool_executes_and_records_once(db_pool):
    shop = await create_shop("S1")
    embedder = FakeEmbedder({
        "七天政策\n七天无理由退货适用于所有自营商品。": unit(0),
        "七天无理由怎么退？": unit(0),
    })
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="seven",
        title="七天政策", filename="seven.md", data=_md("七天政策", "七天无理由退货适用于所有自营商品。"),
        created_by="a", embedder=FakeEmbedder({"七天政策\n七天无理由退货适用于所有自营商品。": unit(0)}),
    )
    conv = await store.create_conversation("c1", shop_id=str(shop["id"]))
    conv_id = str(conv["id"])
    async with db.pool().acquire() as conn:
        seq = await store.add_message(
            conn, conv_id, "assistant",
            {"tool_calls": [{"id": "t1", "name": "search_knowledge", "arguments": {"query": "七天无理由怎么退？"}}]},
        )
    call = {"assistant_seq": seq, "index": 0, "id": "t1", "name": "search_knowledge", "arguments": {"query": "七天无理由怎么退？"}}
    outcome = await loop.execute_tool_call(conv_id, call, lambda s: None, embedder=embedder)
    assert outcome == "executed"
    # 步骤账本：重复执行读 step_result，不重复检索
    outcome2 = await loop.execute_tool_call(conv_id, call, lambda s: None, embedder=embedder)
    assert outcome2 == "executed"
    msgs = await store.get_messages(conv_id)
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1


async def test_search_knowledge_tool_empty_result_message(db_pool):
    shop = await create_shop("S1")
    embedder = FakeEmbedder({"什么问题": unit(0)})
    conv = await store.create_conversation("c1", shop_id=str(shop["id"]))
    conv_id = str(conv["id"])
    async with db.pool().acquire() as conn:
        seq = await store.add_message(
            conn, conv_id, "assistant",
            {"tool_calls": [{"id": "t2", "name": "search_knowledge", "arguments": {"query": "什么问题"}}]},
        )
    call = {"assistant_seq": seq, "index": 0, "id": "t2", "name": "search_knowledge", "arguments": {"query": "什么问题"}}
    await loop.execute_tool_call(conv_id, call, lambda s: None, embedder=embedder)
    msgs = await store.get_messages(conv_id)
    tool_content = [m for m in msgs if m["role"] == "tool"][-1]["content"]
    assert tool_content["text"] and "知识库中无相关内容" in tool_content["text"]
