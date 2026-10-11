# backend/graph/supervisor.py
# ──────────────────────────────────────────────────────────────
#  运营主管 Agent（Supervisor）
#  职责：
#   1. 任务分解：无 DAG 时先调度 planner 生成依赖 DAG
#   2. 条件路由：按 DAG 中「可运行子任务」指派领域 Agent
#   3. 依赖管理：前置任务全部完成后才调度后续任务
#   4. 结果汇总：全部完成后调度 critic 校验并结束
#   5. 异常处理：依赖死锁（连续无可调度任务）时终止；等待审批时挂起
# ──────────────────────────────────────────────────────────────
import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from graph.llm import get_llm
from graph.state import AgentState

DOMAIN_AGENTS = ["selection", "order_logistics", "customer_service", "pricing", "weekly_report"]
ALL_NODES = {"planner", *DOMAIN_AGENTS, "critic", "END"}

# 连续「无可调度子任务」达到该轮数即判定依赖死锁并终止
STUCK_LIMIT = 3

SUPERVISOR_SYSTEM = """
你是电商运营中台的运营主管（Supervisor）。
你管理以下专业 Agent：
- selection          选品分析
- order_logistics    订单与物流
- customer_service   多语言客服
- pricing            定价
- weekly_report      数据周报
- planner            任务规划（把目标拆成 DAG）
- critic             结果校验

根据当前状态决定下一步由谁执行。只回复 JSON：
{"next": "planner"|"selection"|"order_logistics"|"customer_service"|"pricing"|"weekly_report"|"critic"|"END", "reason": "..."}

规则：
- 没有 plan 时先调用 planner。
- 仅当该子任务的所有 depends_on 都完成时才可调度。
- 所有子任务完成后调用 critic。
- 不要重复调度已完成的子任务，避免死循环。
"""


async def supervisor_node(state: AgentState) -> dict:
    plan = state.get("plan", [])
    results = state.get("results", {})

    # ── 等待人工审批 → 本轮挂起 ───────────────────────────────
    if state.get("pending_approval"):
        return {
            "next": "END",
            "messages": [AIMessage(content="存在待审批操作，本轮挂起，等待人工审批。")],
        }

    # ── 已通过校验 → 结束 ─────────────────────────────────────
    if state.get("approved"):
        return {
            "next": "END",
            "messages": [AIMessage(content="运营任务已完成并通过校验。")],
        }

    # ── 尚无 DAG → 先规划 ─────────────────────────────────────
    if not plan:
        return {"next": "planner", "messages": [AIMessage(content="调度 planner 生成运营 DAG")]}

    # ── 按优先级挑选可运行的子任务 ─────────────────────────────
    runnable = []
    for task in sorted(plan, key=lambda t: t.get("priority", 99)):
        tid = task.get("id")
        if tid in results:
            continue
        if not all(d in results for d in task.get("depends_on", [])):
            continue
        runnable.append(task)

    if runnable:
        ticket = runnable[0]
        agent = ticket.get("assigned_to", "")
        if agent not in DOMAIN_AGENTS:
            agent = "selection"
        return {
            "next": agent,
            "stuck_rounds": 0,  # 有进展，重置死锁计数
            "messages": [
                AIMessage(
                    content=f"调度 {agent} 执行子任务 {ticket.get('id')}：{ticket.get('task')}"
                )
            ],
        }

    # ── 全部完成 → 校验 ───────────────────────────────────────
    all_done = all(t.get("id") in results for t in plan)
    if all_done:
        critic_ran = any(
            hasattr(m, "content")
            and ("校验通过" in m.content or "critic approved" in m.content.lower())
            for m in state.get("messages", [])
        )
        if critic_ran:
            return {"next": "END", "messages": [AIMessage(content="运营任务完成。")]}
        return {"next": "critic", "messages": [AIMessage(content="调度 critic 校验运营结果")]}

    # ── 无可调度任务且未完成 → 依赖死锁检测 ───────────────────
    stuck = int(state.get("stuck_rounds", 0)) + 1
    if stuck >= STUCK_LIMIT:
        return {
            "next": "END",
            "stuck_rounds": stuck,
            "messages": [
                AIMessage(
                    content=f"连续 {stuck} 轮无可调度子任务（依赖未满足），判定为依赖死锁，终止流程。"
                )
            ],
        }

    # ── LLM 兜底路由 ──────────────────────────────────────────
    recent = [m.content.lower() for m in state.get("messages", [])[-12:] if hasattr(m, "content")]
    llm = await get_llm(state["user_id"])
    context = (
        f"运营目标:\n{state.get('goal')}\n\n"
        f"已完成: {list(results.keys())}\n\n"
        f"DAG: {json.dumps(plan, ensure_ascii=False)}\n\n"
        f"近况: {recent}"
    )
    response = await llm.ainvoke(
        [
            SystemMessage(content=SUPERVISOR_SYSTEM),
            HumanMessage(content=context),
        ]
    )
    tokens_used = getattr(response, "usage_metadata", {}).get("total_tokens", 0)

    try:
        cleaned = response.content.replace("```json", "").replace("```", "").strip()
        nxt = json.loads(cleaned).get("next", "critic")
        if nxt not in ALL_NODES:
            nxt = "critic"
    except Exception:  # noqa: BLE001
        nxt = "critic"

    return {
        "next": nxt,
        "stuck_rounds": stuck,
        "tokens_used": (state.get("tokens_used") or 0) + tokens_used,
        "messages": [AIMessage(content=f"调度 {nxt}")],
    }
