"""冲突分级（CONTEXT.md「知识库」摄取规则）。

新 chunk 对同店铺 active chunks 的最高余弦相似度 → 处置方式：
- ≥ 0.98：判重复，跳过
- 0.85 ~ 0.98：疑似冲突，文档进冲突队列人工裁决
- < 0.85 或无近邻：直接生效
"""

DUPLICATE_SCORE = 0.98
CONFLICT_SCORE = 0.85


def classify_chunk_conflict(best_score: float | None) -> str:
    if best_score is None:
        return "ok"
    if best_score >= DUPLICATE_SCORE:
        return "duplicate"
    if best_score >= CONFLICT_SCORE:
        return "conflict"
    return "ok"
