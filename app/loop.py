"""自研可恢复 agent loop（ADR-0002）。

不变量（原型推演确立）：
- 每一步完成即落库（write-ahead）：模型的决定先落库，再执行
- 重启重放历史、不重放执行：actions.step 是步骤账本，已完成的步骤直接读
  step_result，绝不重复执行（不重复回复、不重复退款）
- 恢复在任何时刻都合法：resume_loop 从落库状态继续，幂等
"""

import json
from typing import Any, Callable

import litellm

from . import db, store, tools
from .config import settings
from .state import is_complaint, quota_decision

MAX_LOOP_STEPS = 25  # 单回合工具调用次数上限，防失控


async def run_turn(conv_id: str, customer_text: str, emit: Callable[[str], None]) -> dict:
    """顾客消息入口：先落库（write-ahead），再驱动可恢复循环。"""
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            conv = await store.get_conversation_conn(conn, conv_id)
            if conv is None:
                from .errors import NotFound

                raise NotFound("会话不存在")
            if conv["status"] == "transferred":
                from .errors import ConversationTransferred

                raise ConversationTransferred("会话已转人工，顾客消息由坐席回复")
            await store.add_message(conn, conv_id, "customer", {"text": customer_text})
            await store.record(conn, conv_id, "customer", "ask", {"text": customer_text})
            if is_complaint(customer_text):
                await _transfer(conn, conv_id, "关键词命中投诉")
                return {"status": "transferred"}
    return await resume_loop(conv_id, emit)


async def resume_loop(conv_id: str, emit: Callable[[str], None]) -> dict:
    """循环主体。每一圈都从落库状态出发，进程崩溃后重入本函数即可恢复。"""
    for _ in range(MAX_LOOP_STEPS):
        # 1. 已决定、未执行的工具调用先执行（重启恢复走同一条路）
        calls = await store.unexecuted_tool_calls(conv_id)
        if not calls:
            # 2. 没有待执行的决定 → 问模型下一步（先落库决定，不执行）
            conv = await store.get_conversation(conv_id)
            if conv is None or conv["status"] == "transferred":
                return {"status": "transferred"}
            messages = await store.build_llm_messages(conv_id)
            turn = await _chat_step(conv_id, messages, emit)
            if not turn["tool_calls"]:
                return {"status": "completed"}
            continue
        # 3. 执行落库过的工具调用（恰好一次；内部读步骤账本去重）
        for call in calls:
            outcome = await execute_tool_call(conv_id, call, emit)
            if outcome == "parked":
                return {"status": "parked"}
            if outcome == "transferred":
                return {"status": "transferred"}
    return {"status": "max_steps_exceeded"}


async def _chat_step(conv_id: str, messages: list[dict], emit: Callable[[str], None]) -> dict:
    """调模型（流式），把模型的决落库；只决定、不执行。"""
    resp = await litellm.acompletion(
        model=settings.model,
        messages=messages,
        tools=tools.TOOL_SCHEMAS,
        stream=True,
    )
    text_parts: list[str] = []
    calls_acc: dict[int, dict] = {}
    async for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            text_parts.append(content)
            emit(content)
        for tc in getattr(delta, "tool_calls", None) or []:
            acc = calls_acc.setdefault(tc.index, {"id": None, "name": "", "args": ""})
            if tc.id:
                acc["id"] = tc.id
            if tc.function and tc.function.name:
                acc["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                acc["args"] += tc.function.arguments

    text = "".join(text_parts)
    tool_calls = []
    for idx in sorted(calls_acc):
        acc = calls_acc[idx]
        try:
            args = json.loads(acc["args"] or "{}")
        except json.JSONDecodeError:
            args = {"_raw": acc["args"]}
        tool_calls.append({"index": idx, "id": acc["id"], "name": acc["name"], "arguments": args})

    # write-ahead：模型的决先落库；执行永远在下一次循环里、且可恢复
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            if tool_calls:
                seq = await store.add_message(
                    conn, conv_id, "assistant",
                    {"tool_calls": [
                        {"id": c["id"], "name": c["name"], "arguments": c["arguments"]}
                        for c in tool_calls
                    ]},
                )
                for c in tool_calls:
                    c["assistant_seq"] = seq
                await store.record(
                    conn, conv_id, "assistant", "plan_tool_calls",
                    {"tools": [c["name"] for c in tool_calls]},
                )
            else:
                seq = await store.add_message(conn, conv_id, "assistant", {"text": text})
                await store.record(
                    conn, conv_id, "assistant", "reply", {"text": text},
                    step=f"reply:{seq}", step_result={"text": text},
                )
    return {"text": text, "tool_calls": tool_calls}


async def execute_tool_call(
    conv_id: str, call: dict, emit: Callable[[str], None], embedder: Any | None = None,
) -> str:
    """执行一个工具调用，恰好一次。返回 executed | parked | transferred。"""
    step_key = f"tool:{call['assistant_seq']}:{call['index']}"
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            existing = await store.get_step_conn(conn, conv_id, step_key)
            if existing is not None:
                result = dict(existing["step_result"])  # 重放历史，不重放执行
            else:
                result = await _exec_tool(conn, conv_id, call["name"], call.get("arguments") or {}, embedder)
                await store.record(
                    conn, conv_id, "assistant", f"tool:{call['name']}",
                    detail=call.get("arguments") or {}, step=step_key, step_result=result,
                )
            # 工具结果消息（幂等：有则跳过）
            if not await store.has_tool_result(conn, conv_id, call["id"]):
                await store.add_message(
                    conn, conv_id, "tool",
                    {"tool_call_id": call["id"], "text": json.dumps(result, ensure_ascii=False)},
                )
    if result.get("status") == "pending_approval":
        await _park_and_notify(conv_id, result["approval_id"], result["amount"])
        return "parked"
    if result.get("status") == "transferred":
        return "transferred"
    return "executed"


async def _exec_tool(conn: Any, conv_id: str, name: str, args: dict, embedder: Any | None = None) -> dict:
    if name == "get_order":
        return tools.mock_get_order(args.get("order_id", ""))

    if name == "search_knowledge":
        conv = await store.get_conversation_conn(conn, conv_id)
        return await tools.search_knowledge(str(conv["shop_id"]), args.get("query", ""), embedder)

    if name == "create_refund":
        amount = float(args.get("amount", 0))
        quota = await store.get_quota_conn(conn, "refund")
        if quota_decision(amount, quota) == "execute":
            return await tools.execute_refund(conn, conv_id, args.get("order_id", ""), amount)
        # 超额度：生成待审批项，落库，循环退出（parked）—— 领域核心转换
        approval_id = await store.create_pending(conn, conv_id, "create_refund", args, amount)
        await store.record(
            conn, conv_id, "system", "quota_check",
            {"amount": amount, "quota": quota, "decision": "review", "approval_id": approval_id},
        )
        await store.get_or_create_ticket(conn, conv_id)
        return {"status": "pending_approval", "approval_id": approval_id, "amount": amount}

    if name == "transfer_to_human":
        await _transfer(conn, conv_id, args.get("reason", ""))
        return {"status": "transferred"}

    return {"status": "error", "error": f"unknown tool: {name}"}


async def _transfer(conn: Any, conv_id: str, reason: str) -> None:
    """转人工：会话移交坐席；未决待审批项随会话移交，不作废（CONTEXT.md）。"""
    await store.set_conversation_status(conn, conv_id, "transferred")
    await store.transfer_pending_owner_to_seat(conn, conv_id)
    await store.get_or_create_ticket(conn, conv_id)
    await store.record(conn, conv_id, "system", "transfer", {"reason": reason})


async def _park_and_notify(conv_id: str, approval_id: str, amount: float) -> None:
    text = f"您的退款申请（¥{amount:g}）已提交坐席审批，编号 {approval_id}，审批结果会尽快告知您。"
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", conv_id)
            seq = await store.add_message(conn, conv_id, "assistant", {"text": text})
            await store.record(
                conn, conv_id, "assistant", "reply", {"text": text},
                step=f"reply:{seq}", step_result={"text": text},
            )
