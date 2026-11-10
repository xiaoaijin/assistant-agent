# 自研可恢复 agent loop，不用 agent 框架

agent 循环自己写（官方 SDK 直连 `POST /v1/messages` 或经 LiteLLM，见 ADR-0003），不用 LangGraph/PydanticAI 等框架。决定性原因：客服的高风险操作要人工审批，暂停时长以分钟到小时计，进程内循环活不了那么久——"循环的每一圈都可持久化"（待审批时状态落库退出，批准后带原状态重入，借鉴 AutoGPT 的 `_resume` 同 graph_exec_id 重入队思想）是本项目的核心资产，必须是一等语义而不是塞进框架的 checkpoint/interrupt 抽象。Tool Runner（SDK beta 的自动循环）同样因为循环不可跨进程恢复而被排除。

## Consequences

- 恢复语义是**恰好一次的动作**：每一步完成即落库（write-ahead），重启时重放历史但不重放执行——已完成的工具调用、回复、退款在重启后绝不重复执行（2026-11 原型推演中确立：恢复在任何时刻都合法，不仅限于循环退出时）
- 消息持久化、并发轮次准入、断线恢复都要自建（参考 AutoGPT copilot：ChatSession/ChatMessage sequence 唯一键、per-user turn slot、Redis Stream 检查点重放）
- 框架白送的东西（流式封装、状态机）换成少量自研代码
