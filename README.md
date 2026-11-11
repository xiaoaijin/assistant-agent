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
docker compose up -d db redis    # Postgres 16（pgvector）+ Redis 7（SSE 检查点）
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env             # 配置任一 LiteLLM 支持的模型与 key
uvicorn app.main:app --reload --port 8100

pytest                           # 纯领域逻辑测试（无 DB/LLM）
```

## 黄金路径（curl）

```bash
# 0.（RAG）建店铺 + 管理员，上传知识文档（或用管理页 admin-web）
SHOP=$(curl -s localhost:8100/api/admin/shops -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'content-type: application/json' -d '{"name":"默认店铺"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 1. 建会话（shop_id 必填：知识检索按店铺过滤）
CONV=$(curl -s localhost:8100/api/conversations -H 'content-type: application/json' \
  -d "{\"customer_id\":\"c1\",\"shop_id\":\"$SHOP\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

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

- ~~知识检索（RAG）~~ ✅ 已完成（ADR-0005）：pgvector + 三层切块管线 + 冲突队列 + 管理端 SPA（`admin-web/`，`npm run dev` 后访问 localhost:8200）
- ~~审核超时升级~~ ✅ 已完成：`pending_approvals.sla_deadline/escalated_at` + `approvals.sweep_overdue`（标记升级、不自动批准，幂等）；SLA 由 `approval_sla_minutes` 配置
- ~~坐席工作台前端~~ ✅ 已完成：`agent-web/`（`npm run dev` 后访问 localhost:8300）——会话列表（状态筛选 + 待审批角标 + 超时升级红标）、会话详情（消息 + 动作留痕 + 交还）、审批队列（升级置顶 + SLA 倒计时 + 批准/拒绝）
- ~~定时任务（催付/回访）~~ ✅ 已完成：APScheduler + SQLAlchemy jobstore（同一个 Postgres，重启恢复、misfire 宽限补跑）；SLA 每 60s 自动清扫；`POST /api/conversations/{id}/reminders` 排程催付/回访提醒（到点发助手消息，actions.step 幂等）
- ~~消息级断线续传~~ ✅ 已完成：SSE delta 检查点到 Redis Stream（每回合一个 key）；`GET /api/conversations/{id}/stream` 带 `Last-Event-ID` 重连，重放缺失事件并跟随到 done；Redis 不可用时静默退化为全量拉历史
- 召回评估集：合成召回集跑 recall@5，决定是否加混合检索/rerank/分数阈值（等 DASHSCOPE_API_KEY）
- 业务系统真实对接（订单/物流 API + 凭证管理，参考研究笔记 §3.3 两级锁）
