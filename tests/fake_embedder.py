"""确定性假 embedder：预设向量映射，用于驱动冲突/检索测试（1024 维对齐列）。"""

DIM = 1024


def unit(i: int) -> list[float]:
    return [1.0 if j == i else 0.0 for j in range(DIM)]


def cos_pair(a: list[float], b: list[float], cos: float) -> list[float]:
    """构造与 a 余弦恰为 cos 的向量：cos·a + √(1−cos²)·b（a、b 为正交单位向量）。"""
    import math

    s = math.sqrt(1 - cos * cos)
    return [cos * x + s * y for x, y in zip(a, b)]


class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.vectors[t] for t in texts]
