# backend/graph/agents/weekly_report_agent.py
# ──────────────────────────────────────────────────────────────
#  数据周报 Agent
#  职责边界：
#    ✅ 汇总运营数据、生成周报、推送运营负责人
#    ❌ 不执行任何业务写操作（退款/改价/补发）
#  可用工具：db_query(销量/异常/工单/调价) / notify(邮件·Webhook 推送)
#  决策依据：本周销量与 GMV、异常订单数、工单量与情感分布、本周调价次数
#  失败降级：某数据源缺失 → 在报告中标注缺口；推送失败 → 落库待重发
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

REPORT_SYSTEM = """
你是电商的运营数据分析师。请根据本周运营数据，输出一份中文周报，包含：
1) 核心指标：GMV、订单变化、异常订单数、工单量
2) 业务洞察：选品/物流/客服/定价四个维度的关键发现
3) 风险提示：数据缺口、需要关注的异常
4) 下周建议
结构清晰、结论明确，不要编造未提供的数据。
"""

AGENT = "weekly_report"


async def weekly_report_node(state: AgentState) -> dict:
    task = pick_pending(state, AGENT)
    if not task:
        return noop_message(AGENT, "数据周报")

    task_id = state.get("task_id")
    params = state.get("params", {}) or {}
    recipient = params.get("recipient", "运营负责人")

    await emit(events.emit_agent_started, task_id, agent=AGENT, subtask_id=task.get("id"))

    # ── 1) 汇总各维度数据 ─────────────────────────────────────
    sales, _ = await call_tool("db_query", {"table": "sales_daily", "task_id": task_id})
    exceptions, _ = await call_tool("db_query", {"table": "order_exceptions", "task_id": task_id})
    tickets, _ = await call_tool("db_query", {"table": "support_tickets", "task_id": task_id})
    pricing, _ = await call_tool("db_query", {"table": "pricing_history", "task_id": task_id})

    sales_rows = list(getattr(sales, "rows", []) or [])
    gmv = round(sum(float(r.get("revenue_usd", 0) or 0) for r in sales_rows), 2)
    units = sum(int(r.get("units", 0) or 0) for r in sales_rows)

    user_prompt = f"""
运营子任务：{task.get("task")}

{dependency_context(state, task)}

本周销量明细：{to_json(sales_rows[:50])}
本周异常订单：{to_json(list(getattr(exceptions, "rows", []) or [])[:50])}
本周工单：{to_json(list(getattr(tickets, "rows", []) or [])[:50])}
本周调价记录：{to_json(list(getattr(pricing, "rows", []) or [])[:50])}

汇总指标：GMV={gmv} USD，销量={units} 件。

请生成本周运营周报。
"""

    text, tokens = await synthesize(REPORT_SYSTEM, user_prompt, state["user_id"])

    # ── 2) 推送运营负责人 ─────────────────────────────────────
    notify_out, notify_err = await call_tool(
        "notify",
        {
            "channel": params.get("channel", "email"),
            "recipient": recipient,
            "subject": "本周电商运营周报",
            "body": text,
            "task_id": task_id,
        },
    )
    pushed = bool(getattr(notify_out, "success", False))
    if not pushed:
        text += f"\n\n⚠️ 周报推送失败（{notify_err or '未知原因'}），已记录待重发。"

    await emit(
        events.emit_agent_completed,
        task_id,
        agent=AGENT,
        subtask_id=task.get("id"),
        output={"pushed": pushed, "gmv": gmv},
    )

    return build_result(
        state,
        task,
        text,
        tokens_used=tokens,
        executed_ops=[
            {
                "agent": AGENT,
                "op": "weekly_report",
                "gmv_usd": gmv,
                "units": units,
                "pushed": pushed,
                "recipient": recipient,
                "notify_error": notify_err,
            }
        ],
    )
