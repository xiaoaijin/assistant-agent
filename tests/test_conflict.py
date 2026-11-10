"""冲突分级：摄取时新 chunk 与既有 active chunks 的近邻分数 → 处置方式。"""

from app.rag.conflict import classify_chunk_conflict


def test_scores_at_or_above_duplicate_threshold_are_duplicates():
    assert classify_chunk_conflict(0.98) == "duplicate"
    assert classify_chunk_conflict(0.99) == "duplicate"


def test_scores_between_thresholds_are_conflicts():
    assert classify_chunk_conflict(0.9) == "conflict"
    assert classify_chunk_conflict(0.85) == "conflict"


def test_scores_below_conflict_threshold_pass():
    assert classify_chunk_conflict(0.84) == "ok"
    assert classify_chunk_conflict(0.0) == "ok"


def test_no_neighbor_means_ok():
    assert classify_chunk_conflict(None) == "ok"
