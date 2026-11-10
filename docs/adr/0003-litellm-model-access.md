# LiteLLM 作为多模型统一接入层

要求自由选择并配置不同模型（含国内供应商），因此不用任何单一供应商 SDK 锁定接入。选 LiteLLM：一个依赖统一接入 100+ 供应商，覆盖 OpenAI 兼容系（DeepSeek/Qwen/GLM/Kimi）和 Anthropic，并自带 proxy 部署形态。考虑过自研 ModelProvider 端口 + 双适配器（全可控、学习价值最高，但每次加供应商都有接入成本）和聚合网关（OpenRouter 等，运维最省，但多一跳延迟、费用加成、数据出境）。agent loop（ADR-0002）跑在 LiteLLM 之上，不感知具体供应商；模型路由（便宜模型处理 FAQ 流量、强模型处理高风险判断）按任务配置，不写死。
