# backend/graph/agents/pricing_agent.py
# ──────────────────────────────────────────────────────────────
#  定价 Agent
#  职责边界：
#    ✅ 依据库存/销量/竞品价格，用规则引擎给出并（经审批后）执行调价
#    ❌ 不处理订单、退款、客服
#  可用工具：inventory_query / competitor_price / db_query(销量) / pricing_engine / inventory_update(需审批)
#  决策依据：
#    库存水位（>安全水位降价清仓 / 紧张提价保利）
#    竞品均价偏离（>15% 向竞品靠拢）
#    近7天销量（热销小幅上浮）
#    保底价（成本/费率/目标毛利）
#  失败降级：竞品数据缺失 → 按规则保底价；改价幅度超限 → 自动封顶并进入审批
# ──────────────────────────────────────────────────────────────
from bus import events
from config import get_settings
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

PRICING_SYSTEM = """
你是电商的定价策略专家。请基于规则引擎的建议价、库存水位、竞品价格与销量，
输出调价方案报告，包含：
1) 各 SKU 的现价 → 建议价、变动幅度、依据
2) 审批情况：哪些自动执行、哪些提交审批
3) 风险与建议（对毛利与库存的影响）
用中文、条理清晰，不要编造数据。
"""

AGENT = "pricing"

# 演示用 SKU 成本表（mock；真实环境应来自商品主数据）
_MOCK_COST = {
    "SKU-PHONE-CASE-01": 1.20,
    "SKU-CHARGER-20W-02": 2.80,
    "SKU-EARBUDS-A3-03": 6.50,
    "SKU-POWERBANK-10K-04": 5.40,
}

_DEFAULT_SKUS = list(_MOCK_COST.keys())


async def pricing_node(state: AgentState) -> dict:
    task = pick_pending(state, AGENT)
    if not task:
        return noop_message(AGENT, "定价")

    task_id = state.get("task_id")
    params = state.get("params", {}) or {}
    skus = params.get("skus") or _DEFAULT_SKUS

    await emit(events.emit_agent_started, task_id, agent=AGENT, subtask_id=task.get("id"))

    plans: list[dict] = []
    executed_ops: list[dict] = []
    approvals: list[dict] = []
    pending_approval = None

    for sku in skus:
        cost = float(params.get("cost_usd") or _MOCK_COST.get(sku, 3.0))

        inv, _ = await call_tool("inventory_query", {"sku": sku, "task_id": task_id})
        items = list(getattr(inv, "items", []) or [])
        qty = items[0].quantity if items else 0

        cp, _ = await call_tool(
            "competitor_price",
            {
                "sku": sku,
                "market": params.get("market", settings.default_market),
                "task_id": task_id,
            },
        )
        comp_avg = float(getattr(cp, "avg_price", 0) or 0)

        sales, _ = await call_tool(
            "db_query",
            {
                "sql": f"SELECT COALESCE(SUM(units),0) AS units FROM sales_daily WHERE sku = '{sku}'",
                "table": "sales_daily",
                "filters": {"sku": sku},
                "task_id": task_id,
            },
        )
        sales_7d = 0
        rows = list(getattr(sales, "rows", []) or [])
        if rows:
            sales_7d = sum(int(r.get("units", 0) or 0) for r in rows)

        current_price = comp_avg or round(cost * 1.8, 2)

        rule, _ = await call_tool(
            "pricing_engine",
            {
                "sku": sku,
                "cost_usd": cost,
                "current_price_usd": current_price,
                "competitor_avg_usd": comp_avg,
                "inventory_qty": qty,
                "sales_7d": sales_7d,
                "target_margin_pct": float(params.get("target_margin_pct", 30)),
                "task_id": task_id,
            },
        )
        suggested = float(getattr(rule, "suggested_price_usd", current_price) or current_price)
        change_pct = float(getattr(rule, "change_pct", 0) or 0)

        plan = {
            "sku": sku,
            "current_price_usd": current_price,
            "suggested_price_usd": suggested,
            "change_pct": change_pct,
            "rationale": getattr(rule, "rationale", ""),
        }
        plans.append(plan)

        # ── 审批护栏：改价 ────────────────────────────────────
        g = await request_approval_if_needed(
            agent=AGENT,
            operation_type="price_change",
            payload={
                "sku": sku,
                "old_price": current_price,
                "new_price": suggested,
                "change_pct": change_pct,
            },
            state=state,
            task=task,
            amount_usd=abs(suggested - current_price),
            reason=getattr(rule, "rationale", ""),
        )
        approvals.append(g["approval"])

        if g["action"] in ("auto", "approved"):
            executed_ops.append(
                {
                    "agent": AGENT,
                    "op": "price_change",
                    "sku": sku,
                    "old_price": current_price,
                    "new_price": suggested,
                    "change_pct": change_pct,
                    "status": "applied",
                }
            )
        elif g["action"] == "rejected":
            executed_ops.append(
                {"agent": AGENT, "op": "price_change", "sku": sku, "status": "rejected"}
            )
        else:
            pending_approval = g["approval"]
            break

    user_prompt = f"""
运营子任务：{task.get("task")}

{dependency_context(state, task)}

调价方案：{to_json(plans)}
已执行：{to_json(executed_ops)}
审批情况：{to_json([{"sku": a["payload"].get("sku"), "level": a["required_level"], "status": a["status"]} for a in approvals])}

请输出调价方案报告。
"""

    text, tokens = await synthesize(PRICING_SYSTEM, user_prompt, state["user_id"])

    if pending_approval:
        text += f"\n\n⏸ 已提交人工审批：{pending_approval['operation_type']}（{pending_approval['required_level']}），本轮挂起。"

    await emit(
        events.emit_agent_completed,
        task_id,
        agent=AGENT,
        subtask_id=task.get("id"),
        output={"plans": plans},
    )

    return build_result(
        state,
        task,
        text,
        tokens_used=tokens,
        executed_ops=executed_ops,
        approvals=approvals,
        pending_approval=pending_approval,
    )
