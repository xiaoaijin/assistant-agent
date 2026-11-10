# 电商智能客服（assistant）

独立新项目：全智能接管客服岗位，高风险场景（投诉、超额资金操作）人工介入。

- 领域词汇：[`CONTEXT.md`](CONTEXT.md)（会话、工单、客服助手、坐席、操作、额度、审批、转人工、动作留痕…）
- 架构决策：[`docs/adr/`](docs/adr/)（聊天优先无 DAG 引擎 / 自研可恢复 loop / LiteLLM 多模型 / Postgres+pgvector 单库）
- AutoGPT 借鉴研究：[`docs/engineering/customer-service-agent-research.md`](docs/engineering/customer-service-agent-research.md)
- 状态机原型：`proto/loop-state-model` 分支（用后即弃，主分支只保留验证过的决策）

## 架构

```
顾客 ⇄ SSE ⇄ FastAPI ──→ 自研可恢复 agent loop（app/loop.py）
                          ├─ LiteLLM（模型无关，自由换供应商）
                          ├─ 工具：get_order / create_refund / transfer_to_human
                          │        └─ 额度判定（state.quota_decision）
                          ├─ 超额度 → 待审批项落库 → 循环退出（parked）
                          └─ 坐席批准 → 循环重入 → 恰好一次执行退款
坐席 API：/api/approvals/{id}/approve|reject、/api/conversations/{id}/handback
```

核心不变量（ADR-0002，原型推演确立）：

1. **每一步完成即落库**（write-ahead）：模型的决定先落库，再执行
2. **重启重放历史、不重放执行**：`actions.step` 是步骤账本 + 唯一键，已完成步骤直接读 `step_result`，绝不重复回复、不重复退款
3. **恢复在任何时刻都合法**：`POST /api/conversations/{id}/resume` 幂等
4. **转人工不是终态**：坐席交还后助手重新介入；未决待审批项随会话移交、不能在未决时交还

## 运行

```bash
docker compose up -d db          # Postgres 16（pgvector 镜像）
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env             # 配置任一 LiteLLM 支持的模型与 key
uvicorn app.main:app --reload --port 8100

pytest                           # 纯领域逻辑测试（无 DB/LLM）
```

## 黄金路径（curl）

```bash
# 1. 建会话
CONV=$(curl -s localhost:8100/api/conversations -H 'content-type: application/json' \
  -d '{"customer_id":"c1"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 2. 问物流（SSE 流式回复，模型调用 get_order mock）
curl -N localhost:8100/api/conversations/$CONV/messages \
  -H 'content-type: application/json' -d '{"text":"我的订单A1024到哪了"}'

# 3. 要求退款 500（额度 100 → 超额度 → 待审批项落库，循环 parked）
curl -N localhost:8100/api/conversations/$CONV/messages \
  -H 'content-type: application/json' -d '{"text":"我不想要了，退我500块"}'

# 4. 看状态与动作留痕（可追溯）
curl localhost:8100/api/conversations/$CONV/state
curl localhost:8100/api/conversations/$CONV/audit

# 5. 坐席批准 → 循环重入 → 退款恰好一次执行，工单已解决
curl -X POST localhost:8100/api/approvals/PA-*/approve   # 从 /api/approvals?status=pending 取 id

# 6.（可选）坐席交还 → 顾客再发消息，助手重新介入
curl -X POST localhost:8100/api/conversations/$CONV/handback

# 7.（随时可用）崩溃恢复：幂等
curl -N -X POST localhost:8100/api/conversations/$CONV/resume
```

## 待办（按优先级）

- 知识检索（RAG）：UnifiedContentEmbedding 建模 + 文档切片（见研究笔记 §3.2）
- 坐席工作台前端 + 会话列表
- 业务系统真实对接（订单/物流 API + 凭证管理，参考研究笔记 §3.3 两级锁）
- 审核超时升级（SLA 内未审批自动升级）——AutoGPT 没有的部分（研究笔记 §5）
- 定时任务（催付/回访）：APScheduler + SQL jobstore 模式
- 消息级断线续传（Redis Stream 检查点重放，研究笔记 §3.1）
