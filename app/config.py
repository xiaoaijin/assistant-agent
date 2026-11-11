from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://assistant:assistant@localhost:5433/assistant"

    # LiteLLM 模型串，自由更换供应商；agent loop 不感知具体供应商（ADR-0003）
    model: str = "deepseek/deepseek-chat"

    system_prompt: str = (
        "你是电商店铺的客服助手，替店铺面对顾客，负责售前咨询、订单查询、退款等。"
        "规则：\n"
        "1. 查询订单用 get_order；顾客要求退款用 create_refund（amount 为退款金额，单位元）。\n"
        "2. 退款有额度限制：超额度会自动转坐席审批，你只需告知顾客已提交审批。\n"
        "3. 顾客威胁投诉、要求真人时，调用 transfer_to_human 并说明原因。\n"
        "4. 回复用简体中文，简洁友好，不编造订单信息。"
    )

    default_refund_quota: float = 100.0

    # 审批 SLA：待审批项创建起算（日历分钟数，不做工作时间），超时升级见 approvals.sweep_overdue
    approval_sla_minutes: int = 60

    # RAG（ADR-0005）：通义 text-embedding-v3，1024 维，经 LiteLLM
    embedding_model: str = "dashscope/text-embedding-v3"
    embedding_dim: int = 1024
    search_top_k: int = 5
    search_min_score: float | None = None  # 余弦阈值，召回评估后启用
    admin_secret: str = "dev-admin-secret-change-me"


settings = Settings()
