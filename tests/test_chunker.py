"""S1 切块器：纯函数 golden 测试（无 DB、无网络）。

文档 → DocNode 树（parse_document）→ chunk 列表（chunk_document）。
断言切出数量、标题路径、内容不丢。
"""

import pytest

from app.rag.chunker import chunk_document, parse_document


def test_markdown_single_section_is_one_chunk():
    md = (
        "# 售后政策\n"
        "\n"
        "七天无理由退货适用于所有自营商品。\n"
        "\n"
        "退货商品需保持吊牌完整。\n"
    ).encode()
    doc = parse_document("policy.md", md)
    chunks = chunk_document("policy", doc)
    assert len(chunks) == 1
    assert chunks[0].title_path == "售后政策"
    assert chunks[0].text.startswith("售后政策\n")
    assert "七天无理由退货适用于所有自营商品。" in chunks[0].text
    assert "退货商品需保持吊牌完整。" in chunks[0].text


def test_markdown_nested_headings_split_sections_with_full_title_path():
    md = (
        "# 售后政策\n"
        "\n"
        "本政策适用于全部自营订单。\n"
        "\n"
        "## 退货\n"
        "\n"
        "七天无理由退货适用于所有自营商品。\n"
        "\n"
        "### 七天无理由\n"
        "\n"
        "签收后 7 天内可申请，商品需不影响二次销售。\n"
        "\n"
        "## 质量问题\n"
        "\n"
        "质量问题退货运费由店铺承担。\n"
    ).encode()
    doc = parse_document("policy.md", md)
    chunks = chunk_document("policy", doc)
    # 4 个 section：根前言、退货、七天无理由、质量问题
    assert len(chunks) == 4
    paths = [c.title_path for c in chunks]
    assert paths == [
        "售后政策",
        "售后政策 > 退货",
        "售后政策 > 退货 > 七天无理由",
        "售后政策 > 质量问题",
    ]
    assert chunks[2].text.startswith("售后政策 > 退货 > 七天无理由\n")
    assert "质量问题退货运费由店铺承担。" in chunks[3].text


def test_long_section_splits_at_paragraph_boundaries():
    para = "退" * 250  # 两个段落 501 字 > 500 上限 → 每段一个 chunk
    md = f"# 政策\n\n{para}甲。\n\n{para}乙。\n\n{para}丙。\n".encode()
    doc = parse_document("policy.md", md)
    chunks = chunk_document("policy", doc)
    assert len(chunks) == 3
    assert all(c.title_path == "政策" for c in chunks)
    assert "甲。" in chunks[0].text and "乙。" not in chunks[0].text
    assert "丙。" in chunks[2].text
    assert all(c.char_count <= 500 for c in chunks)


def test_single_huge_paragraph_sliding_window_with_overlap():
    body = "".join(str(i % 10) for i in range(1200))  # 1200 字单段 → 400/50 滑窗
    md = f"# 政策\n\n{body}\n".encode()
    doc = parse_document("policy.md", md)
    chunks = chunk_document("policy", doc)
    # 窗口：[0:400] [350:750] [700:1100] [1050:1200]
    assert len(chunks) == 4
    assert chunks[0].text.removeprefix("政策\n") == body[0:400]
    assert chunks[1].text.removeprefix("政策\n").startswith(body[350:400])
    assert chunks[2].text.removeprefix("政策\n").startswith(body[700:750])
    assert chunks[3].text.removeprefix("政策\n") == body[1050:1200]
    assert all(c.char_count <= 500 for c in chunks)


def test_markdown_table_linearized_as_own_chunk():
    md = (
        "# 运费说明\n"
        "\n"
        "以下为各区域运费标准。\n"
        "\n"
        "| 区域 | 首重 | 续重 |\n"
        "| --- | --- | --- |\n"
        "| 华东 | 5 元 | 1 元 |\n"
        "| 华北 | 6 元 | 2 元 |\n"
    ).encode()
    doc = parse_document("policy.md", md)
    chunks = chunk_document("policy", doc)
    # 段落一个 chunk，表格独立一个 chunk
    assert len(chunks) == 2
    table = chunks[1]
    assert table.title_path == "运费说明"
    assert "| 区域 | 首重 | 续重 |" in table.text
    assert "| 华东 | 5 元 | 1 元 |" in table.text
    assert "| 华北 | 6 元 | 2 元 |" in table.text


def test_faq_yaml_one_chunk_per_qa_pair():
    data = (
        "- question: 七天无理由退货怎么申请？\n"
        "  answer: 在订单详情页点击申请售后，选择退货退款即可。\n"
        "- question: 退货运费谁承担？\n"
        "  answer: 七天无理由退货运费由买家承担，质量问题由店铺承担。\n"
    ).encode()
    doc = parse_document("faq.yaml", data)
    chunks = chunk_document("faq", doc)
    assert len(chunks) == 2
    assert chunks[0].text == "Q: 七天无理由退货怎么申请？\nA: 在订单详情页点击申请售后，选择退货退款即可。"
    assert "运费谁承担" in chunks[1].text


def test_plain_text_first_line_title_and_numbered_headings():
    txt = (
        "售后政策\n"
        "\n"
        "七天无理由退货适用于所有自营商品。\n"
        "退货商品需保持吊牌完整。\n"
        "\n"
        "一、退货流程\n"
        "\n"
        "第一步：在订单详情页申请售后。\n"
        "\n"
        "二、退款时效\n"
        "\n"
        "退款将在审核通过后 1-3 个工作日原路退回。\n"
    ).encode()
    doc = parse_document("policy.txt", txt)
    chunks = chunk_document("policy", doc)
    assert [c.title_path for c in chunks] == [
        "售后政策",
        "售后政策 > 一、退货流程",
        "售后政策 > 二、退款时效",
    ]
    assert "退货商品需保持吊牌完整。" in chunks[0].text
    assert "第一步：在订单详情页申请售后。" in chunks[1].text


def _make_pdf(pages: list[list[tuple[float, str]]]) -> bytes:
    """用 pymupdf 生成测试 PDF：每页是 (字号, 文本) 列表。"""
    import pymupdf

    doc = pymupdf.open()
    for page_specs in pages:
        page = doc.new_page()
        y = 80
        for size, text in page_specs:
            page.insert_text((72, y), text, fontsize=size, fontname="china-s")
            y += size + 14
    return doc.tobytes()


def test_pdf_font_size_cluster_infers_heading_levels_and_pages():
    pdf = _make_pdf(
        [
            [
                (18, "售后政策"),
                (10.5, "七天无理由退货适用于所有自营商品。"),
                (14, "退货流程"),
                (10.5, "第一步：在订单详情页申请售后。"),
            ],
            [
                (14, "退款时效"),
                (10.5, "退款将在审核通过后 1-3 个工作日原路退回。"),
            ],
        ]
    )
    doc = parse_document("faq.pdf", pdf)
    chunks = chunk_document("faq", doc)
    assert [c.title_path for c in chunks] == [
        "售后政策",
        "售后政策 > 退货流程",
        "售后政策 > 退款时效",
    ]
    assert "七天无理由退货适用于所有自营商品。" in chunks[0].text
    assert chunks[0].meta["page"] == 1
    assert "第一步：在订单详情页申请售后。" in chunks[1].text
    assert chunks[2].meta["page"] == 2  # 跨页标题正确带页码
    assert "1-3 个工作日" in chunks[2].text


def test_pdf_scanned_document_is_rejected_with_clear_error():
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 300, 400), pixmap=pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100)))
    pdf = doc.tobytes()
    with pytest.raises(ValueError, match="扫描件"):
        parse_document("scan.pdf", pdf)
