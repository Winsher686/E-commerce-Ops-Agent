# backend/graph/agents/selection_agent.py
# ──────────────────────────────────────────────────────────────
#  选品分析 Agent
#  职责边界：
#    ✅ 市场调研、竞品分析、利润测算、选品建议
#    ❌ 不负责订单处理、退款、改价执行、客服回复
#  可用工具：market_data / competitor_price / fx_rate / profit_calculator
#  决策依据：市场需求指数与增长、竞品价格分布、单位毛利与盈亏平衡价
#  失败降级：市场数据缺失 → 降低置信度并标注；竞品为空 → 改用需求驱动建议
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
    synthesize,
    to_json,
)
from graph.state import AgentState

settings = get_settings()

SELECTION_SYSTEM = """
你是电商的资深选品分析师。请基于给定的市场数据、竞品价格与利润测算，
输出一份结构化的选品分析报告，包含：
1) 市场机会：需求指数、增长趋势、季节性与重点子类目
2) 竞品格局：价格区间、竞争激烈程度
3) 利润测算：以建议售价计算的单位毛利与盈亏平衡价
4) 选品建议：明确的推荐/不推荐结论 + 理由 + 风险提示
请用中文、条理清晰，不要编造未提供的数据；数据缺失时请明确指出。
"""

AGENT = "selection"


async def selection_node(state: AgentState) -> dict:
    task = pick_pending(state, AGENT)
    if not task:
        return noop_message(AGENT, "选品分析")

    task_id = state.get("task_id")
    params = state.get("params", {}) or {}
    market = params.get("market", settings.default_market)
    category = params.get("category") or task.get("task", "")
    sku = params.get("sku", "")
    cost_usd = float(params.get("cost_usd", 0) or 0)

    await emit(events.emit_agent_started, task_id, agent=AGENT, subtask_id=task.get("id"))

    # ── 工具调用 ──────────────────────────────────────────────
    md, md_err = await call_tool(
        "market_data",
        {
            "market": market,
            "category": category,
            "task_id": task_id,
        },
    )
    cp, cp_err = await call_tool(
        "competitor_price",
        {
            "sku": sku or category,
            "market": market,
            "task_id": task_id,
        },
    )

    # 竞品均价 → 利润测算（成本缺失时按均价 45% 估算）
    comp_avg = float(getattr(cp, "avg_price", 0) or 0)
    est_cost = cost_usd or round(comp_avg * 0.45, 2) or 5.0
    est_price = comp_avg or round(est_cost * 1.8, 2)
    profit, _ = await call_tool(
        "profit_calculator",
        {
            "cost_usd": est_cost,
            "price_usd": est_price,
            "task_id": task_id,
        },
    )

    # ── 降级提示 ──────────────────────────────────────────────
    degraded = []
    if md is None:
        degraded.append(f"市场数据获取失败({md_err})，结论置信度降低")
    if cp is None or not getattr(cp, "items", None):
        degraded.append(f"竞品数据缺失({cp_err})，改用需求驱动建议")

    user_prompt = f"""
运营子任务：{task.get("task")}
目标市场：{market}
品类/关键词：{category}
SKU：{sku or "（未指定）"}

{dependency_context(state, task)}

市场数据：{to_json(md)}
竞品价格：{to_json(cp)}
利润测算（示例成本 {est_cost}，示例售价 {est_price}）：{to_json(profit)}
数据降级说明：{"；".join(degraded) if degraded else "无"}

请输出选品分析报告与明确建议。
"""

    text, tokens = await synthesize(SELECTION_SYSTEM, user_prompt, state["user_id"])

    await emit(
        events.emit_agent_completed,
        task_id,
        agent=AGENT,
        subtask_id=task.get("id"),
        output={"degraded": degraded},
    )

    return build_result(
        state,
        task,
        text,
        tokens_used=tokens,
        executed_ops=[
            {
                "agent": AGENT,
                "subtask_id": task.get("id"),
                "tools": ["market_data", "competitor_price", "profit_calculator"],
            }
        ],
    )
