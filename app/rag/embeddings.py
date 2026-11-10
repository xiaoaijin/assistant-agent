"""嵌入客户端：生产走 LiteLLM（通义 text-embedding-v3，ADR-0005），测试注入 fake。

接口约定：embed(texts) 与输入等长返回向量列表，维度 = settings.embedding_dim。
"""

from typing import Protocol

from ..config import settings


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LiteLLMEmbedder:
    """生产实现：litellm.aembedding，模型无关（ADR-0003）。"""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import litellm

        resp = await litellm.aembedding(
            model=settings.embedding_model,
            input=list(texts),
            dimensions=settings.embedding_dim,
        )
        return [item["embedding"] for item in resp.data]
