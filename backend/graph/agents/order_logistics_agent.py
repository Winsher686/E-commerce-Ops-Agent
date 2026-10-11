# backend/graph/agents/order_logistics_agent.py
# ──────────────────────────────────────────────────────────────
#  订单与物流 Agent
#  职责边界：
#    ✅ 订单状态查询、物流异常分类、补发/退款决策与执行（含审批）
#    ❌ 不负责选品、定价、客服话术生成（通知草稿可产出，但由客服 Agent 润色）
#  可用工具：order_query / logistics_track / inventory_query / refund(需审批) / inventory_update(需审批)
#  决策依据：
#    丢件      → 有库存则补发；无库存则退款
#    超时未达  → 催促 + 视金额小额补偿
#    拒收退回  → 退款
#    地址错误  → 通知客服确认地址
#  失败降级：物流查询失败 → 标记转人工；无库存 → 退款；超阈值 → 进入审批流程
# ──────────────────────────────────────────────────────────────
from config import get_settings
from bus import events
from graph.agents._base import (
    build_result,
    call_tool,
    dependency_context,
    emit,
    noop_message,
    pick_pending,
    request_approval_if_needed,
    synthesize,
    to_json,
)
from graph.state import AgentState

settings = get_settings()

ORDER_SYSTEM = """
你是跨境电商的订单与物流运营专家。请根据异常订单与物流轨迹，输出处理报告，包含：
1) 异常订单清单：订单号、异常类型、金额
2) 处置动作：补发 / 退款 / 催促 / 转人工，及依据
3) 审批情况：哪些操作已自动执行、哪些已提交人工审批、哪些被拒绝
4) 后续建议
用中文、条理清晰。不要编造未提供的数据。
"""

AGENT = "order_logistics"

# 异常类型 → 处置动作
_ACTION_BY_EXCEPTION = {
    "丢件": "reship_or_refund",
    "超时未达": "urge_and_compensate",
    "拒收退回": "refund",
    "地址错误": "contact_cs",
    "清关滞留": "urge",
}


async def order_logistics_node(state: AgentState) -> dict:
    task = pick_pending(state, AGENT)
    if not task:
        return noop_message(AGENT, "订单与物流")

    task_id = state.get("task_id")
    params = state.get("params", {}) or {}
    limit = int(params.get("limit", 10))

    await emit(events.emit_agent_started, task_id, agent=AGENT, subtask_id=task.get("id"))

    # ── 1) 查询订单 ───────────────────────────────────────────
    orders_out, orders_err = await call_tool("order_query", {
        "order_ids": params.get("order_ids", []),
        "platform": params.get("platform"),
        "status": params.get("status"),
        "limit": limit,
        "task_id": task_id,
    })
    orders = list(getattr(orders_out, "orders", []) or [])

    executed_ops: list[dict] = []
    approvals: list[dict] = []
    pending_approval = None
    findings: list[dict] = []

    # ── 2) 逐单追踪物流并分类异常 ─────────────────────────────
    for o in orders[:limit]:
        track, track_err = await call_tool("logistics_track", {
            "order_id": o.order_id, "task_id": task_id,
        })
        is_exc = bool(getattr(track, "is_exception", False)) or bool(track_err)
        if not is_exc:
            continue

        exc_type = getattr(track, "exception_type", "") or "未知异常"
        action = _ACTION_BY_EXCEPTION.get(exc_type, "manual")
        amount = float(getattr(o, "amount_usd", 0) or 0)
        findings.append({
            "order_id": o.order_id, "exception": exc_type,
            "amount_usd": amount, "action": action,
        })

        # ── 3) 决策执行（含审批护栏）────────────────────────
        if action in ("refund",) or (action == "reship_or_refund"):
            # 有库存则优先补发，否则退款
            use_reship = False
            if action == "reship_or_refund":
                inv, _ = await call_tool("inventory_query", {
                    "sku": o.sku, "task_id": task_id,
                })
                items = list(getattr(inv, "items", []) or [])
                use_reship = bool(items and items[0].quantity > 0)

            if use_reship:
                g = await request_approval_if_needed(
                    agent=AGENT, operation_type="reship",
                    payload={"order_id": o.order_id, "sku": o.sku, "count": 1,
                             "amount_usd": amount},
                    state=state, task=task, amount_usd=amount,
                    reason=f"{o.order_id} 丢件补发",
                )
            else:
                g = await request_approval_if_needed(
                    agent=AGENT, operation_type="refund",
                    payload={"order_id": o.order_id, "amount_usd": amount, "method": "original"},
                    state=state, task=task, amount_usd=amount,
                    reason=f"{o.order_id} {exc_type} 退款",
                )

            approvals.append(g["approval"])

            if g["action"] in ("auto", "approved"):
                if not use_reship:
                    res, _ = await call_tool("refund", {
                        "order_id": o.order_id, "amount_usd": amount,
                        "reason": exc_type, "task_id": task_id,
                    })
                    executed_ops.append({
                        "agent": AGENT, "op": "refund", "order_id": o.order_id,
                        "success": bool(getattr(res, "success", False)),
                        "refund_id": getattr(res, "refund_id", ""),
                    })
                else:
                    executed_ops.append({
                        "agent": AGENT, "op": "reship", "order_id": o.order_id, "success": True,
                    })
            elif g["action"] == "rejected":
                executed_ops.append({
                    "agent": AGENT, "op": "skipped", "order_id": o.order_id,
                    "reason": "审批被拒绝",
                })
            else:  # pending —— 需要人工审批，挂起本轮
                pending_approval = g["approval"]
                break

        elif action == "urge_and_compensate":
            executed_ops.append({"agent": AGENT, "op": "urge", "order_id": o.order_id})
        elif action == "contact_cs":
            executed_ops.append({"agent": AGENT, "op": "forward_to_cs", "order_id": o.order_id})

    # ── 4) 生成处理报告 ───────────────────────────────────────
    user_prompt = f"""
运营子任务：{task.get('task')}

{ dependency_context(state, task) }

订单总数：{len(orders)}（订单查询错误：{orders_err or '无'}）
异常发现：{to_json(findings)}
已执行操作：{to_json(executed_ops)}
审批情况：{to_json([{ 'operation_type': a['operation_type'], 'status': a['status'], 'level': a['required_level'] } for a in approvals])}

请输出订单与物流异常处理报告。
"""

    text, tokens = await synthesize(ORDER_SYSTEM, user_prompt, state["user_id"])

    if pending_approval:
        text += f"\n\n⏸ 已提交人工审批：{pending_approval['operation_type']}（{pending_approval['required_level']}），本轮挂起。"

    await emit(events.emit_agent_completed, task_id, agent=AGENT,
               subtask_id=task.get("id"), output={"findings": findings})

    return build_result(
        state, task, text,
        tokens_used=tokens,
        executed_ops=executed_ops,
        approvals=approvals,
        pending_approval=pending_approval,
    )
