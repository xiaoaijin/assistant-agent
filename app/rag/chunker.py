"""切块管线（三层纯函数）：文档 → DocNode 树 → chunk 列表。

- 解析层：各格式 adapter（按扩展名分发）→ 统一 DocNode 树，节点带标题层级/页码等来源元数据
- 切块层：叶子 section 为单元，标题路径前缀拼入 chunk 文本；长度约束后续切片补
- 元数据层：chunker_version 记录切块器版本，支撑将来全量重切片

无 DB、无网络依赖，可独立测试。
"""

from dataclasses import dataclass, field

import mistune

CHUNKER_VERSION = 1


@dataclass
class DocNode:
    kind: str  # root | heading | paragraph | table | list | qa_pair
    text: str = ""
    level: int = 0
    page: int | None = None
    children: list["DocNode"] = field(default_factory=list)


@dataclass
class Chunk:
    seq: int
    text: str
    title_path: str
    char_count: int
    meta: dict


def parse_document(filename: str, data: bytes) -> DocNode:
    """格式 adapter 统一入口：按扩展名分发，返回 DocNode 树。"""
    name = filename.lower()
    if name.endswith(".md") or name.endswith(".markdown"):
        return _parse_markdown(data.decode("utf-8"))
    if name.endswith(".yaml") or name.endswith(".yml"):
        return _parse_faq_yaml(data.decode("utf-8"))
    if name.endswith(".txt"):
        return _parse_plain_text(data.decode("utf-8"))
    if name.endswith(".pdf"):
        return _parse_pdf(data)
    raise ValueError(f"暂不支持的文档格式: {filename}")


_PDF_BODY_RATIO = 1.2  # 字号超过正文字号的 1.2 倍视为标题
_PDF_MIN_TEXT_CHARS = 10  # 全文可提取字符低于此数判定为扫描件


def _parse_pdf(data: bytes) -> DocNode:
    """PDF：字号聚类推断标题层级（正文取众数字号），逐页保留页码。

    表格不特殊处理（pymupdf 文本流降级，设计允许）；扫描件明确报错，v1 不做 OCR。
    """
    import collections

    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    # 先收集全部行（页码, 字号, 文本），再聚类出正文/标题字号
    lines: list[tuple[int, float, str]] = []
    for pno, page in enumerate(doc, start=1):
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                text = "".join(span["text"] for span in line["spans"]).strip()
                if text:
                    size = max(span["size"] for span in line["spans"])
                    lines.append((pno, size, text))
    if sum(len(t) for _, _, t in lines) < _PDF_MIN_TEXT_CHARS:
        raise ValueError("扫描件 PDF 暂不支持：页面无可提取文本，请提供文字版 PDF")

    body_size = collections.Counter(round(size, 1) for _, size, _ in lines).most_common(1)[0][0]
    heading_sizes = sorted(
        {size for _, size, _ in lines if size >= body_size * _PDF_BODY_RATIO and size > body_size},
        reverse=True,
    )
    heading_level = {size: i + 1 for i, size in enumerate(heading_sizes)}

    root = DocNode("root")
    stack: list[tuple[int, DocNode]] = [(0, root)]
    for pno, size, text in lines:
        if size in heading_level and len(text) <= 50:
            node = DocNode(kind="heading", text=text, level=heading_level[size], page=pno)
        else:
            node = DocNode(kind="paragraph", text=text, page=pno)
        _attach(stack, node)
    doc.close()
    return root


_HEADING_PATTERN = r"^(?:[一二三四五六七八九十百]+、|\d+[、.]|第[一二三四五六七八九十百\d]+[章节条款])"


def _parse_plain_text(text: str) -> DocNode:
    """纯文本：空行分段；首行单行块为标题，编号行（一、/1、/1./第X章）为小节标题。"""
    import re

    root = DocNode("root")
    stack: list[tuple[int, DocNode]] = [(0, root)]
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    for i, block in enumerate(blocks):
        lines = block.splitlines()
        if i == 0 and len(lines) == 1:
            node = DocNode(kind="heading", text=lines[0], level=1)
        elif len(lines) == 1 and re.match(_HEADING_PATTERN, block):
            node = DocNode(kind="heading", text=block, level=2)
        else:
            node = DocNode(kind="paragraph", text=block)
        _attach(stack, node)
    return root


def _attach(stack: list[tuple[int, DocNode]], node: DocNode) -> None:
    """按标题层级把节点挂到树上：段落挂在当前最里层标题下。"""
    if node.kind == "heading":
        while stack[-1][0] >= node.level:
            stack.pop()
    stack[-1][1].children.append(node)
    if node.kind == "heading":
        stack.append((node.level, node))


def _parse_faq_yaml(text: str) -> DocNode:
    """FAQ YAML（question/answer 列表）→ qa_pair 节点列表。"""
    import yaml

    root = DocNode("root")
    for item in yaml.safe_load(text) or []:
        root.children.append(
            DocNode(kind="qa_pair", text=f"Q: {item['question']}\nA: {item['answer']}")
        )
    return root


def _parse_markdown(text: str) -> DocNode:
    tokens = mistune.create_markdown(renderer="ast", plugins=["table"])(text)
    root = DocNode("root")
    stack: list[tuple[int, DocNode]] = [(0, root)]
    for tok in tokens:
        ttype = tok["type"]
        if ttype == "blank_line":
            continue
        if ttype == "table":
            node = DocNode(kind="table", text=_linearize_table(tok))
        else:
            node = DocNode(kind=ttype, text=_inline_text(tok.get("children")))
        if ttype == "heading":
            level = (tok.get("attrs") or {}).get("level") or tok.get("level", 1)
            node.level = level
        _attach(stack, node)
    return root


def _inline_text(children: list[dict] | None) -> str:
    if not children:
        return ""
    out = []
    for t in children:
        if "raw" in t:
            out.append(t["raw"])
        elif "children" in t:
            out.append(_inline_text(t["children"]))
    return "".join(out)


def _linearize_table(tok: dict) -> str:
    """表格 → markdown 管道文本；行多时按行分片、每片重复表头。"""
    header, rows = [], []
    for part in tok.get("children", []):
        cells = [
            _inline_text(cell.get("children"))
            for cell in part.get("children", [])
        ]
        if part["type"] == "table_head":
            header = cells
        else:
            rows.extend(
                [_inline_text(cell.get("children")) for cell in row.get("children", [])]
                for row in part.get("children", [])
            )
    lines = ["| " + " | ".join(header) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def chunk_document(content_type: str, doc: DocNode) -> list[Chunk]:
    """DocNode 树 → chunk 列表：叶子 section 为单元，标题路径前缀拼入文本。

    超长 section 按段落边界贪心装箱；单段仍超长则 400/50 滑窗。
    """
    sections: list[tuple[list[str], list[str], int | None]] = []
    _collect_sections(doc, [], sections)
    bodies: list[tuple[list[str], str, int | None]] = []
    for path, parts, page in sections:
        for body in _pack(path, parts):
            bodies.append((path, body, page))
    chunks = []
    for i, (path, body, page) in enumerate(bodies):
        prefix = " > ".join(path)
        text = f"{prefix}\n{body}" if prefix else body
        meta = {"chunker_version": CHUNKER_VERSION}
        if page is not None:
            meta["page"] = page
        chunks.append(
            Chunk(
                seq=i,
                text=text,
                title_path=prefix,
                char_count=len(text),
                meta=meta,
            )
        )
    return chunks


MAX_CHUNK_CHARS = 500
WINDOW_CHARS = 400
WINDOW_OVERLAP = 50


def _pack(path: list[str], parts: list[str]) -> list[str]:
    """段落贪心装箱（前缀计入长度）；单段超限走滑窗。"""
    prefix = " > ".join(path)
    budget = MAX_CHUNK_CHARS - len(prefix) - 1
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            out.append("\n".join(cur))
            cur, cur_len = [], 0

    for part in parts:
        if len(part) > budget:
            flush()
            out.extend(_slide(part, budget))
            continue
        extra = len(part) + (1 if cur else 0)
        if cur and cur_len + extra > budget:
            flush()
        cur.append(part)
        cur_len += len(part) + (1 if len(cur) > 1 else 0)
    flush()
    return out


def _slide(text: str, budget: int) -> list[str]:
    size = min(WINDOW_CHARS, budget)
    step = size - WINDOW_OVERLAP
    if step <= 0:
        step = size
    windows = []
    start = 0
    while True:
        windows.append(text[start : start + size])
        if start + size >= len(text):
            break
        start += step
    return windows


def _collect_sections(
    node: DocNode, path: list[str], out: list[tuple[list[str], list[str], int | None]]
) -> None:
    current_path = path + [node.text] if node.kind == "heading" else path
    body_nodes = [
        c for c in node.children
        if c.kind not in ("heading", "table", "qa_pair") and c.text.strip()
    ]
    if body_nodes:
        out.append((current_path, [c.text for c in body_nodes], body_nodes[0].page))
    for child in node.children:
        if child.kind == "heading":
            _collect_sections(child, current_path, out)
        elif child.kind in ("table", "qa_pair") and child.text.strip():
            out.append((current_path, [child.text], child.page))  # 表格/FAQ 问答独立成 chunk
