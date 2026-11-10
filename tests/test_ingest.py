"""S2 摄取 seam：ingest_document → active | pending_review | duplicate，冲突三档用假 embedder 驱动。"""

import pytest

from app.rag.ingest import ingest_document
from app.rag.store import create_shop
from tests.fake_embedder import FakeEmbedder, cos_pair, unit

MD = "# 政策\n\n七天无理由退货适用于所有自营商品。\n".encode()


async def test_new_document_becomes_active_with_chunks_stored(db_pool):
    shop = await create_shop("测试店")
    embedder = FakeEmbedder({"政策\n七天无理由退货适用于所有自营商品。": unit(0)})
    result = await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="policy_v1",
        title="售后政策", filename="policy.md", data=MD, created_by="admin1",
        embedder=embedder,
    )
    assert result["status"] == "active"
    assert result["chunks_stored"] == 1
    assert result["chunks_skipped"] == 0


async def test_same_source_key_reingest_replaces_chunks(db_pool):
    shop = await create_shop("测试店")
    embedder = FakeEmbedder({"政策\n七天无理由退货适用于所有自营商品。": unit(0)})
    first = await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="policy_v1",
        title="售后政策", filename="policy.md", data=MD, created_by="admin1",
        embedder=embedder,
    )
    second = await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="policy_v1",
        title="售后政策V2", filename="policy.md", data=MD, created_by="admin1",
        embedder=embedder,
    )
    assert second["document_id"] == first["document_id"]  # 同 key 更新而非新增
    assert second["status"] == "active"
    assert second["chunks_stored"] == 1


async def test_nearly_identical_new_document_is_marked_duplicate(db_pool):
    shop = await create_shop("测试店")
    base = unit(0)
    embedder = FakeEmbedder({
        "政策\n七天无理由退货适用于所有自营商品。": base,
        "政策A\n七天无理由退货适用于所有自营商品。": cos_pair(base, unit(1), 0.99),
    })
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="a",
        title="A", filename="a.md",
        data="# 政策\n\n七天无理由退货适用于所有自营商品。\n".encode(),
        created_by="admin1", embedder=embedder,
    )
    result = await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="b",
        title="A 重复版", filename="b.md",
        data="# 政策A\n\n七天无理由退货适用于所有自营商品。\n".encode(),
        created_by="admin1", embedder=embedder,
    )
    assert result["status"] == "duplicate"
    assert result["chunks_stored"] == 0


async def test_conflicting_document_goes_to_review_queue(db_pool):
    shop = await create_shop("测试店")
    base = unit(0)
    embedder = FakeEmbedder({
        "政策\n退货时限为七天。": base,
        "新政策\n退货时限为三十天。": cos_pair(base, unit(1), 0.9),
    })
    await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="old",
        title="旧政策", filename="old.md",
        data="# 政策\n\n退货时限为七天。\n".encode(),
        created_by="admin1", embedder=embedder,
    )
    result = await ingest_document(
        shop_id=str(shop["id"]), content_type="policy", source_key="new",
        title="新政策", filename="new.md",
        data="# 新政策\n\n退货时限为三十天。\n".encode(),
        created_by="admin1", embedder=embedder,
    )
    assert result["status"] == "pending_review"
    assert result["chunks_stored"] == 1  # 片段落库，等待裁决；检索只查 active


async def test_unsupported_format_raises_and_persists_nothing(db_pool):
    shop = await create_shop("测试店")
    with pytest.raises(ValueError, match="暂不支持"):
        await ingest_document(
            shop_id=str(shop["id"]), content_type="policy", source_key="x",
            title="X", filename="x.docx", data=b"whatever",
            created_by="admin1", embedder=FakeEmbedder({}),
        )
