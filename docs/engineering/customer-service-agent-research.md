# 通用电商智能客服 Agent：AutoGPT Platform 可借鉴子系统研究

## 1. 研究问题

要做一个"全智能接管客服岗位"的 agent（售前咨询、订单查询、退换货、物流跟踪、催付等），高风险场景（投诉、退款超额、法律/舆情）转人工。本笔记在 AutoGPT Platform 仓库里逐个核实可借鉴的子系统——HITL 审核门、聊天会话与流式回复、事件总线、凭证管理、计费、知识检索、定时调度——每个结论都落到一手代码（file:line，基于 2026-11 的 master，commit 9cb76f9026 附近），并给出"直接复用 / 改造参考 / 仅借鉴思想"的判断。

## 2. 客服 agent 需要的能力清单（按优先级）

1. **会话管理 + 流式回复** —— 多渠道会话、消息持久化、SSE/WS 流式输出、断线续传
2. **知识检索（RAG）** —— FAQ、商品信息、售后政策的向量检索
3. **业务系统对接** —— 订单/物流/退款 API 的凭证管理、OAuth/API key 生命周期
4. **HITL 升级人工** —— 高风险动作暂停为待审状态、人工批准/拒绝后恢复执行、审批幂等与自动审批
5. **安全与审计** —— 多租户隔离、敏感操作开关、消息与工具调用留痕
6. **成本核算** —— LLM/token 成本记账（内部系统可不向用户收费，但要按店铺/会话核算成本）
7. **调度与定时** —— 催付、回访、超时未回复提醒等定时任务

## 3. 能力 → AutoGPT 实现（file:line）

### 3.1 会话管理 + 流式回复（最贴近客服形态的参考：copilot 聊天，不是 graph 引擎）

**数据模型**（`autogpt_platform/backend/schema.prisma`）

- `ChatSession` :864 —— 会话表：`userId`、`title`、`expertId`、`metadata Json`、token 累计（`totalPromptTokens/totalCompletionTokens`）、租户（`organizationId/teamId/visibility`）、置顶/分享字段。生命周期用 `chatStatus`（`idle/queued/running`）表达，`:889-897` 注释说明 `updatedAt` 兼作状态变更时间戳。
- `ChatMessage` :1004 —— 消息表：`role/content/toolCalls`、`sequence` 且 `@@unique([sessionId, sequence])`（:1059，保证会话内顺序与写幂等）、`model/routingSource/traceId`（审计该轮实际用的模型与路由）、`metadata Json`（存排队轮次的原始提交负载以便重放）。

**并发轮次准入**（`backend/backend/copilot/active_turns.py`）

- `acquire_turn_slot` :186 —— async context manager，`admit_chat_session_turn` 在一个 per-user 数据库锁里"计数+翻转"（模块 docstring :23-25），并发提交恰好填满空闲槽位，既不超卖也不漏放。三种结果：admitted / refreshed（同 session 重试）/ rejected（`ConcurrentTurnLimitError` :110）。
- 孤儿恢复：`ORPHAN_RUNNING_RESET_SECONDS` :143 —— running 超过 30s 且无活跃流即视为孤儿，读会话时释放，后台兜底扫描。
- `MAX_TURN_LIFETIME_SECONDS` :51（6h 上限，只监控不硬杀）。

客服对应：一个客服坐席/一个店铺的并发会话上限、排队提示、断线后状态回收，这一套几乎可以照搬。

**流式回复**（`backend/backend/api/features/chat/routes.py` + `backend/backend/copilot/stream_registry.py`）

- `stream_chat_post` :1920 —— POST 发消息即返回 SSE（`text/event-stream`，Vercel AI SDK UI message stream 格式：文本片段、tool-call UI、tool 结果）。生成在后台任务里跑，客户端断开不中断；`GET /sessions/{session_id}/stream`（`resume_session_stream` :2538）断线续传。
- `copilot/stream_registry.py` —— **Redis Stream（不是 pub/sub）** 做会话级流注册表：
  - `publish_chunk` :366、`subscribe_to_session` :551、`subscribe_to_turn` :619
  - `TurnCheckpoint` :142 + `_trim_to_last_checkpoint` :1342 —— 只保留最后检查点，重连从检查点重放（`_replay` :726），不重发整轮
  - `sse_frame` :674 把内部 chunk 编成 SSE 帧
  - `mark_session_completed` :1125 收尾并归还 turn slot
- `copilot/transports.py` :82 `get_chat_transports` —— 会话可切换 LLM 传输通道（按凭证有效性过滤），客服多渠道（网页/App/企微）可参考这个"transport 注册表"模式。

**与 graph 执行的关系**：聊天轮次可以触发后台 subtask / graph 执行（`copilot/background_execution.py` :25 `claim_background_execution`、:132 `finish_background_execution`；`ChatBackgroundTask` 模型 schema.prisma:979；`background_subtasks.py`）。客服的"查订单/发起退款"就是这类子任务。

**借鉴方式：直接复用/轻改造。** ChatSession+ChatMessage+turn slot+Redis Stream 续传是现成的会话客服底座。多渠道接入时只需在 `stream_chat_post` 外再包一层渠道 webhook 适配器。

### 3.2 知识检索（RAG）

- `UnifiedContentEmbedding`（schema.prisma:3122）—— 统一嵌入表：`embedding Unsupported("vector(1536)")` pgvector 列（:3136）、`@@unique([contentType, contentId, userId])`（:3143）、search 用 GIN 索引经 SQL migration 建（:3140 注释）。新内容类型只需扩 `ContentType` 枚举。
- `backend/backend/api/features/search/embeddings.py`
  - `generate_embedding` :124（text-embedding-3-small，1536 维，必须匹配 pgvector 列维度 :144-145）
  - `semantic_search` :735 —— pgvector 余弦 `1 - (embedding <=> ...)`（:837-855），原生 SQL 因为 Prisma 不支持 pgvector（:262）
- `backend/backend/copilot/chat_session_embeddings.py` :55 —— 会话标题 fire-and-forget 嵌入（强引用 task set 防止 GC，:21）。

**重要局限**：AutoGPT 只对会话**标题**和库/店铺内容做嵌入，**消息正文没有 RAG**，也没有"上传文档→切片→FAQ 检索"链路。

**借鉴方式：改造参考。** 拿 UnifiedContentEmbedding + semantic_search 当骨架，新增 `PRODUCT_FAQ` / `POLICY` ContentType，自己补文档切片与召回评估。也可直接换独立向量库，仅借鉴"统一嵌入表 + ContentType"的数据建模。

### 3.3 业务系统对接（订单/物流 API 凭证）

- `backend/backend/integrations/creds_manager.py` :100 `IntegrationCredentialsManager` —— 类 docstring（:106-120）写清了两级锁设计：*获取*可能触发刷新（刷新=失效+替换），所以 get 也要写锁；`refresh` 细粒度锁防止并发重复刷新；`!time_sensitive` 锁让更新优先于获取，避免长队阻塞刷新。
  - `get` :154 —— oauth2 按 `access_token_expires_at` 自动刷新
  - `acquire` :193 —— ⚠️ 系统级租约锁：凭证同时只能被一处使用（一次 block 执行）
- `backend/backend/integrations/credential_lease.py` :10 `CredentialLease` —— 长任务租约：`start_heartbeat` :27（oauth2 心跳协程）、`validate` :32（锁所有权丢失即失败）、`checkpoint` :46（任务中途提交轮换后的新 token）。执行器拿到租约后（executor/manager.py ~:663，见锚点），即使 token 在任务中途被刷新也能续上。
- 存储：`integrations/credentials_store.py`（per-user 加密存取，Prisma `IntegrationCredentials` 模型）；webhook 注册表 `integrations/webhooks/__init__.py` :12 `load_webhook_managers`（provider→manager 注册，物流状态回调可参考）。

**借鉴方式：直接复用** `IntegrationCredentialsManager` + `CredentialLease`（对接淘宝/抖店/Shopify 等 OAuth 平台时）；API key 类客服场景可简化掉租约心跳，只留加密存取 + 刷新锁。

### 3.4 HITL 升级人工（投诉转人工的最直接参考）

**暂停判定**（`backend/backend/blocks/_base.py`）

- Block 构造参数 `is_irreversible_action` :628/:676 —— "效果已到达平台之外"的动作（发消息、退款）标记为不可逆。
- `is_block_exec_need_review` :904 —— 只有 `is_irreversible_action AND execution_context.sensitive_action_safe_mode`（:926-929）才进审核；在 `_execute` :988（graph 执行内）自动调用；拒绝即抛 `BlockExecutionError` :958-964 中止执行。
- `sensitive_action_safe_mode` 来源：`GraphSettings`（`backend/backend/data/graph.py` :91-94，per-graph 开关，默认 False；`human_in_the_loop_safe_mode` 默认 True）；进入执行上下文 `ExecutionContext`（`backend/backend/data/execution.py` :116，:146 `graph_sensitive_action_safe_mode` 存原值防 chat 覆写）。
- 专用人工输入块：`backend/backend/blocks/human_in_the_loop.py` :22 `HumanInTheLoopBlock`。

**审核落库与恢复**

- `backend/backend/blocks/helpers/review.py`
  - `HITLReviewHelper` :30；`_handle_review_request` :69 —— **幂等**：先 `check_approval` :117 查已有审批（含自动审批：`node_exec_id != 当前` 判定 :132），有则直接带原/新数据放行；无则建 `PendingHumanReview` 行并把 node 置 `ExecutionStatus.REVIEW`（:182-185），返回 None 让 block 静默 return（暂停）。
  - `_gate_review` :197 —— 聊天 gate（`backend/backend/copilot/gate/workflow.py`）先自动裁决：`review_route` :50 找到会话 → `judge_step` :72 用规则/LLM 判定 → 放行则 `record_gate_approval`；不放行则 `open_step_card` :112 在聊天里开审批卡并 "parked"。
  - `handle_review_decision` :270 —— 单次调用返回"继续/暂停/拒绝"决策。
- 存储模型：`PendingHumanReview`（schema.prisma:2359）—— `nodeExecId @id`（每节点执行一行）、`payload Json`（待审数据）、`editable`、`status ReviewStatus`、`processed`（引擎是否已消费结果）、归属 **graph 三元组或 chatSessionId 二选一**（check constraint）。
- 恢复执行：`backend/backend/api/features/graph_executions/review/routes.py`（`process_review_action` :161、按会话列出待审 :130）→ `service.py`
  - `process_reviews` :66 —— 批量批准/拒绝 → `_record_auto_approvals` :254（"批准未来同类操作"按 node_id 去重落自动审批记录）→ `_resume_if_nothing_pending` :325 全部答完后 `_resume` :347 用**同一个 graph_exec_id** 重新 `add_graph_execution`（executor/utils.py:1597），暂停的步骤重跑并在 review.py:117 命中已审批继续。
  - 防重：RUNNING 中的 run 拒绝作答（409，:101-107）；状态必须 REVIEW/INCOMPLETE（:241-251）。
- 执行器侧：manager.py:211 QUEUED 与 REVIEW 一起调度；:1379 REVIEW 状态处理；:279 `BILLED_REDISPATCH_STATUSES = (REVIEW, RUNNING)` —— 重派时不再重复扣费（:1560-1629 注释）。

**借鉴方式：直接复用思想 + 改造实现。** 客服转人工就是同一模式换个触发器：不是 per-block 声明，而是风险分类器（投诉/超额/舆情）触发；审核人不一定是会话主人而是客服坐席；需要补坐席工作台和超时升级（AutoGPT 没有审核 SLA/超时自动放行）。`PendingHumanReview` 的幂等键（node_exec_id 唯一）、`processed` 两阶段标记、auto-approve-future 都值得原样照搬。

### 3.5 事件总线与实时通信（两条并存的链路）

**graph 执行事件**（客服子任务执行状态推送可参考）

- `backend/backend/data/execution.py` —— `ExecutionEventType` :1657；频道命名 `exec_channel` :1700 / `graph_all_channel` :1704（user_id 前缀隔离）；`RedisExecutionEventBus` :1708（同步）；**`AsyncRedisExecutionEventBus` :1764**（`publish` :1772、`listen` :1812、`listen_graph` :1821），继承通用 `AsyncRedisEventBus`。
- WebSocket 消费端：`backend/backend/api/ws_api.py` :112 `handle_subscribe`（⚠️ 图读取权限校验被注释掉 :128-135，生产要补）；`WebsocketServer(AppProcess)` :368；鉴权 `authenticate_websocket` :69。连接管理 `backend/backend/api/conn_manager.py` :83 `ConnectionManager`（`subscribe_graph_exec` :147、`_subscribe` :217 把 Redis pub/sub 桥接到 WS、断流清理 :259）。

**聊天流**（见 3.1）：注意 copilot **不用**上面的 pub/sub，而用 Redis Stream + checkpoint 重放——因为聊天需要断线续传，pub/sub 无重放能力。

**借鉴方式：仅借鉴思想。** 客服若走 SSE（推荐，参考 3.1 的 stream_registry），WS 总线只需在"坐席工作台实时看会话状态"时再考虑；直接用 `AsyncRedisExecutionEventBus` 推子任务状态即可。

### 3.6 多租户、安全与审计

- `ExecutionContext`（`backend/backend/data/execution.py` :116 附近）贯穿 `user_id / organization_id / team_id`，从 API 层一直带到 block 执行；`PendingHumanReview`、`ChatSession`、`IntegrationWebhook` 等模型全部带 organizationId/teamId 冗余列。
- JWT：`autogpt_libs/auth`（`Security(auth.get_user_id)` 用法见 chat/routes.py:1922-1923）；FastAPI 依赖注入 + `Security()` 保证 OpenAPI 安全标注。
- 审计留痕：`ChatMessage.model/routingSource/traceId`（schema.prisma:1030-1044）记录每轮实际模型与 PostHog trace；`ChatSession.metadata Json` 可扩展。
- 缓存保护中间件：`backend/backend/api/middleware/security.py`（全端点默认 `no-store`，白名单放行）——客服聊天数据同样不可缓存，可直接沿用。

**借鉴方式：直接复用**（单店铺可退化为单 org；多商户 SaaS 则 org 模型现成）。

### 3.7 成本核算

- `backend/backend/executor/billing.py` —— 两段式：
  - `charge_usage` :122 **预扣费**：执行前按 BLOCK_COSTS 估额 `spend_credits` / `spend_org_credits`（:156/:164，org 钱包由 `billed_org_id` :143 决定——多商户成本分摊的现成模式）
  - `charge_reconciled_usage` :326 **对账**：按实际 token 统计结算差额
  - 动态成本守卫：`_block_has_paid_cost_entry` :79 + :171-179 —— 预估为 0 但存在计费条目的块，余额不足不许启动，防止对账阶段坏账
- `backend/backend/data/credit.py` :775 `InsufficientBalanceError`；`_settle_node`（executor/manager.py:1105）在节点结束时对账；REVIEW/RUNNING 重派防重复扣费（manager.py:279, :1560-1629）。
- `backend/backend/copilot/token_tracking.py` + `ChatSession.totalPromptTokens/totalCompletionTokens`（schema.prisma:906-907）—— 会话级 token 记账。

**借鉴方式：仅借鉴思想。** 客服大概率不向用户收费，但"预扣→对账"两段式 + 会话级 token 累计是准确的成本核算方案；"余额不足不启动付费动作"可改造成"预算门"（`copilot/budget_gate.py` 已有类似概念）。

### 3.8 调度与定时（催付/回访）

- 独立服务进程：`backend/backend/scheduler.py` —— `run_processes(Scheduler())`。
- `backend/backend/executor/scheduler.py` :2936 `class Scheduler(AppService)` —— APScheduler `BackgroundScheduler` + **SQL 持久化 jobstore**（`ResilientSQLAlchemyJobStore` :2940，重启不丢任务）；cron/interval/date 三种触发器（:30-33）；:856 每任务支持多 cron。
- 任务体即"触发一次 graph 执行/聊天轮"，同样要过 turn slot 准入（:752-758 注释：拿不到槽位时 cron 任务保持注册、下个 tick 重试）；graph 调度 API 在 `backend/backend/api/features/schedules/`；cron 合法性检查 `backend/backend/util/cron.py`。

**借鉴方式：直接复用/轻改造。** 催付、回访、超时未回复提醒 = 每单一个 date/cron job，jobstore 持久化 + 错过重试的语义现成。

## 4. 建议简化的部分（AutoGPT 有但客服不需要）

- **可视化 graph builder / 市场商店**（`api/features/store`、frontend builder）—— 客服用代码定义固定流程（FAQ→查单→退款分级）即可，无需 DAG 编辑器、模板、分享。
- **专家平台 / 技能目录**（`skills-catalog` 仓库、`backend/backend/api/features/experts`、roster 拉取与 lazy 升级）—— 客服不需要"24 个人格化专家"和技能热更新。
- **租约心跳**（`CredentialLease.start_heartbeat`）—— 客服单次 API 调用毫秒级完成，两级锁可简化为普通获取+惰性刷新。
- **面向终端用户的计费/订阅/发票**（Stripe、trial、top-up，`data/credit.py` 大部分）—— 内部系统只留成本记账。
- **执行事件 WS 总线**（若前端全走 SSE）。
- **通用 block 生态**（上百个第三方集成块）—— 只需订单/物流/IM 几个 provider。

## 5. 需要新增的部分（AutoGPT 没有）

- **工单系统**：会话之外的问题单（跨会话、有状态流转、SLA）。AutoGPT 的 PendingHumanReview 是"执行级暂停"不是"工单"。
- **多渠道接入层**：网页/APP/企微/淘宝旺旺等渠道 webhook 适配 + 渠道侧 ack/重推语义（`integrations/webhooks/` 的 provider-manager 注册模式可参考，但聊天渠道要自己写）。
- **坐席工作台与转人工路由**：风险分类器（投诉/超额/舆情）→ 升级；人工接管后 AI 静默/半静默（copilot 的 `turn_pause.py`、审批卡 "parked" 是雏形）；审核超时自动升级策略完全没有。
- **知识库管理后台**：文档上传→切片→索引→召回评估，AutoGPT 只有嵌入表和查询函数。
- **客服质检**：会话抽样评分、违规话术检测——可复用 ChatMessage 的 traceId/model 字段做数据源，但功能本身要新建。
- **消息正文 RAG**：见 3.2，消息级语义检索要自建。
- **WS 订阅鉴权**：ws_api.py:128-135 的图权限校验是注释掉的，若复用 WS 必须补齐。

## 6. 开放问题

1. **架构选型：聊天优先还是流程优先？** copilot 聊天栈（ChatSession/turn slot/Redis Stream）最贴近客服形态，但其 LLM 循环与 copilot 工具体系耦合深；graph 引擎适合"退款分级"这类确定性流程。建议混合：聊天栈做会话与流式，子任务按类型选择"直接函数调用"或"graph 执行"。需要原型验证 `background_execution.py` 的 claim/finish 契约能否脱离 copilot 工具体系复用。
2. **转人工的审核人模型**：PendingHumanReview 绑定 `userId`（数据主人），客服场景审核人是坐席而非客户本人——权限模型要改成按店铺/角色（org+role），复用 organizationId 列但需新的授权检查。
3. **自动审批的安全边界**：`auto-approve-future`（按 node_id）对客服是否危险？退款类操作按"金额阈值+订单状态"自动放行比按节点更合理，需要自定义放行策略层。
4. **RAG 选型**：沿用 pgvector（运维简单、与 Prisma 同库）还是独立向量库（切片/混合检索生态）？1536 维列已定死，换模型要迁移。
5. **成本归属粒度**：`billed_org_id` 的组织钱包模式是否足够，还是需要按"店铺/会话/问题类型"多维记账？
6. **定时任务的错过语义**：APScheduler + SQL jobstore 的 misfire 策略要按业务确认（催付错过 30 分钟还发吗？）。
