# RAG 模块：切块管线、冲突队列与管理端

知识检索（RAG）在 pgvector（ADR-0004）之上的模块设计，经 2026-11-10 设计会话逐轮确认。

## 决策

1. **v1 知识范围**：FAQ + 售后政策。商品信息走实时工具（会过期，不属于向量检索）；历史工单检索作为下一个 ContentType 扩展。
2. **接入 loop 的方式**：工具 `search_knowledge(query)`，模型自主决定何时查——与 get_order 同构（ADR-0001 chat-first），动作留痕天然覆盖（`tool:search_knowledge` 入步骤账本），可恢复语义不变。
3. **检索方式**：纯 pgvector 余弦 top-k=5 起步，不做硬分数阈值（`min_score` 进 Settings 默认 None）。是否加混合检索/rerank 由合成召回集的 recall@5 评估结果决定，不为未验证的问题上复杂度。
4. **嵌入模型**：通义 `text-embedding-v3`（1024 维），经 LiteLLM（ADR-0003）。embedding API 不进任何测试：检索/摄取代码一律注入 embedder 接口，测试用确定性 fake。
5. **切块管线**（三层纯函数，`app/rag/chunker.py`）：
   - 解析层 `(filename, bytes) → DocNode 树`：markdown（mistune AST）、纯文本（空行分段 + 编号行标题启发式）、FAQ YAML（qa_pair）、PDF（pymupdf 字号聚类推断标题层级、保留页码；扫描件无可提取文本即明确报错，v1 不做 OCR）。docx/HTML 留接口后补。
   - 切块层：叶子 section 为单元，标题路径（`售后政策 > 退货 > 七天无理由`）前缀拼入 chunk 文本；≤500 字上限，超长按段落边界贪心装箱，单段超长走 400/50 滑窗；表格线性化为 markdown 管道文本独立成 chunk；FAQ 一问一答一 chunk。
   - 元数据层：`chunker_version` 落库，支撑切块器升级后全量重切片。
6. **冲突处理**：同 `source_key` 重传 = 更新（同事务先删后插）。不同 key：摄取时每个新 chunk 对同店铺 active chunks 近邻检索——cos ≥ 0.98 判重跳过；0.85 ≤ cos < 0.98 文档置 `pending_review` 进冲突队列，管理页新旧对照人工裁决（新生效旧 superseded，或拒绝新文档）；< 0.85 直接 `active`。检索只查 `active`。客服答错政策的代价高，故选人工审队列而非仅警告。
7. **摄取语义**：同步（请求内完成切片+嵌入，v1 文档量小、操作者是管理员）；失败直接报错给操作者。
8. **管理端**：独立 SPA（Vite + React + TS，`assistant/admin-web/`），dev 走 Vite proxy，不做 Jinja2 服务端渲染——管理页要长成可交付的产品（店铺管理、冲突队列、chunk 预览）。鉴权：`shops` + `admin_users` 表（pbkdf2 哈希），`admin_users.shop_id` 为空即平台超管可跨店；HMAC 签名 token（签名不加密）。普通管理员只见本店知识库。

## Consequences

- 检索质量评估是后续必做项：合成召回集（20–50 条顾客问法 + 期望命中 chunk）跑 recall@5，作为是否引入混合检索/rerank/阈值的依据
- 嵌入模型换型 = 全量重切片重灌（原文在 `knowledge_documents`，可执行）
- 扫描件 PDF 报错而非静默返回空，管理员可感知
- 会话与知识库通过 `conversations.shop_id` 关联，`search_knowledge` 按会话所属店铺过滤
