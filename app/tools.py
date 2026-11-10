"""工具定义与执行。

工具是普通函数；额度、审批、转人工这些领域规则包在执行处（ADR-0001）。
模型决定"做什么"，能不能做由这里说了算。
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": "查询订单信息（状态、可退金额）",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string", "description": "订单号"}},
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_refund",
            "description": "发起退款。超额度会自动转坐席审批。",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "订单号"},
                    "amount": {"type": "number", "description": "退款金额（元）"},
                },
                "required": ["order_id", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "检索店铺知识库（FAQ、售后政策等）。顾客咨询政策、流程、规则类问题时调用；每次换一个更精确的问法可再次检索。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "检索问句，用顾客问题的关键词改写"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transfer_to_human",
            "description": "将会话转人工坐席。顾客威胁投诉（如打 12315）、明确要求真人、涉及法律风险时调用。",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "转人工原因"}},
                "required": ["reason"],
            },
        },
    },
]

# ---- mock 业务数据（真实实现替换为业务系统 API + 凭证管理） ----

MOCK_ORDER = {"order_id": "A1024", "status": "已发货", "refundable_amount": 500, "shipping": "预计明天送达"}


async def search_knowledge(shop_id: str, query: str, embedder=None) -> dict:
    """知识库检索：有命中返回片段，无命中返回固定文案（模型据此告知顾客/转人工）。"""
    from .rag.embeddings import LiteLLMEmbedder
    from .rag.search import semantic_search

    results = await semantic_search(shop_id, query, embedder or LiteLLMEmbedder())
    if not results:
        return {"status": "no_results", "message": "知识库中无相关内容"}
    return {
        "status": "ok",
        "results": [
            {"title_path": r["title_path"], "text": r["text"],
             "score": round(r["score"], 4), "page": r["page"]}
            for r in results
        ],
    }


def mock_get_order(order_id: str) -> dict:
    return {**MOCK_ORDER, "order_id": order_id or MOCK_ORDER["order_id"]}


async def execute_refund(conn, conv_id: str, order_id: str, amount: float) -> dict:
    """退款执行的桩：真实实现接支付网关。"""
    from . import store

    await store.record(conn, conv_id, "system", "refund_executed", {"order_id": order_id, "amount": amount})
    return {"status": "refunded", "order_id": order_id, "amount": amount}
