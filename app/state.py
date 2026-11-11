"""纯领域逻辑：从落库数据推导状态、做规则判定。

这是原型（proto/loop-state-model 分支）验证过的状态机的可持久化形态：
无 IO、无进程内状态，一切从 actions / pending_approvals 推导。
"""

# 投诉识别的关键词兜底（模型侧的 transfer_to_human 是主判定，这里是确定性后盾）
COMPLAINT_KEYWORDS = ("投诉", "12315", "工商局", "消协", "曝光", "媒体", "举报")


def is_complaint(text: str) -> bool:
    return any(k in text for k in COMPLAINT_KEYWORDS)


def quota_decision(amount: float, quota: float) -> str:
    """规则额度制：额度内直接执行，超出走审批。"""
    return "execute" if amount <= quota else "review"


def escalation_status(now, sla_deadline, escalated_at) -> str:
    """审批 SLA 判定：超时未升级 → overdue，升级过 → escalated。

    sla_deadline 为空（旧数据/未设 SLA）永不升级。
    """
    if escalated_at is not None:
        return "escalated"
    if sla_deadline is None or now < sla_deadline:
        return "on_time"
    return "overdue"


def can_handback(conv_status: str, pending_status: str | None) -> tuple[bool, str]:
    """坐席交还规则：只在人工处理中、且没有未决待审批项时允许。"""
    if conv_status != "transferred":
        return False, "会话不在人工处理中"
    if pending_status == "pending":
        return False, "待审批项未决，坐席不能交还（没人兜底的悬挂操作）"
    return True, ""


def derive_loop_status(conv_status: str, pending_status: str | None) -> str:
    if conv_status == "transferred":
        return "exited_transferred"
    if pending_status == "pending":
        return "exited_pending"
    return "idle"


def derive_state(
    conv_status: str,
    actions: list[dict],
    pending: dict | None,
) -> dict:
    """可观测状态（GET /state 用）。actions 按 seq 升序，元素含 actor/action/step。

    turn_steps = 最近一个顾客回合内已完成步骤（每步完成即落库；重启重放历史、不重放执行）。
    """
    pending_status = pending["status"] if pending else None
    loop = derive_loop_status(conv_status, pending_status)

    last_ask = max(
        (i for i, a in enumerate(actions) if a["actor"] == "customer"),
        default=-1,
    )
    turn_steps = [a["step"] for a in actions[last_ask + 1 :] if a.get("step")]

    return {
        "conversation": conv_status,
        "loop": loop,
        "loop_persisted": True,  # 每步完成即落库；只要有历史即可恢复
        "turn_steps": turn_steps,
        "pending": pending,
    }
