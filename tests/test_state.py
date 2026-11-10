"""纯领域逻辑测试（无 DB、无 LLM）。

对应原型 proto/loop-state-model 四个引导场景的可持久化形态。
"""

from app.state import can_handback, derive_state, is_complaint, quota_decision


def test_quota_decision_follows_rule():
    assert quota_decision(100, 100) == "execute"
    assert quota_decision(99, 100) == "execute"
    assert quota_decision(100.01, 100) == "review"
    assert quota_decision(500, 100) == "review"


def test_complaint_keywords():
    assert is_complaint("再不解决我就打12315")
    assert is_complaint("我要投诉你们")
    assert not is_complaint("麻烦帮我看下物流")


def _actions(*items):
    return [{"actor": a, "action": x, "step": s} for a, x, s in items]


def test_golden_path_state():
    actions = _actions(
        ("customer", "ask", None),
        ("assistant", "tool:get_order", "查订单"),
        ("assistant", "reply", "回复顾客"),
        ("customer", "ask", None),
        ("system", "quota_check", "额度判定→生成 PA-1，循环退出"),
    )
    st = derive_state("ai_serving", actions, {"id": "PA-1", "status": "pending", "owner": "conversation"})
    assert st["loop"] == "exited_pending"
    # 本轮已完成步骤只含最近一个顾客回合之后的步骤
    assert st["turn_steps"] == ["额度判定→生成 PA-1，循环退出"]
    assert st["loop_persisted"] is True


def test_after_approval_loop_is_idle():
    actions = _actions(
        ("customer", "ask", None),
        ("system", "quota_check", "额度判定→生成 PA-1，循环退出"),
        ("seat", "approve", "退款执行（重入后完成）"),
        ("assistant", "reply", "回复顾客"),
    )
    st = derive_state("ai_serving", actions, {"id": "PA-1", "status": "executed", "owner": "seat"})
    assert st["loop"] == "idle"


def test_transfer_carries_pending_as_exited_transferred():
    actions = _actions(
        ("customer", "ask", None),
        ("system", "quota_check", "额度判定→生成 PA-1，循环退出"),
        ("system", "transfer", "转人工（待审批项随会话移交）"),
    )
    st = derive_state("transferred", actions, {"id": "PA-1", "status": "pending", "owner": "seat"})
    assert st["loop"] == "exited_transferred"


def test_handback_rules():
    ok, reason = can_handback("transferred", "pending")
    assert not ok and "未决" in reason
    ok, _ = can_handback("transferred", None)
    assert ok
    ok, reason = can_handback("ai_serving", None)
    assert not ok and "人工" in reason


def test_empty_state():
    st = derive_state("ai_serving", [], None)
    assert st["loop"] == "idle"
    assert st["turn_steps"] == []
    assert st["pending"] is None
