# backend/graph/agents/planner.py
# ──────────────────────────────────────────────────────────────
#  运营 DAG 规划器
#  将运营目标分解为“依赖感知的任务 DAG”，每个子任务指派给领域 Agent。
#  输出字段：id / task / assigned_to / depends_on / priority(1-5)
#
#  assigned_to 取值（领域 Agent）：
#    selection          选品分析
#    order_logistics    订单与物流
#    customer_service   多语言客服
#    pricing            定价
#    weekly_report      数据周报
# ──────────────────────────────────────────────────────────────
import json
import re

import structlog
from langchain_core.messages import AIMessage

from graph.llm import get_llm
from graph.state import AgentState

log = structlog.get_logger()

PLANNER_SYSTEM = """
你是电商运营中台的任务规划器。
请把用户的运营目标拆解为“依赖感知的任务 DAG”，每个子任务指派给一个专业 Agent。

可用专业 Agent（assigned_to 只能取以下值）：
- selection          选品分析：市场调研、竞品分析、利润测算、选品建议
- order_logistics    订单与物流：订单查询、物流异常处理、补发/退款决策
- customer_service   多语言客服：工单分类、情感分析、回复草稿
- pricing            定价：依据库存/销量/竞品动态调整定价
- weekly_report      数据周报：汇总运营数据、生成周报、推送

规划规则：
1. 只使用上述 assigned_to 取值，禁止臆造新 Agent。
2. 用 depends_on 表达前置依赖：后续任务必须在依赖任务完成后才执行。
3. 无依赖关系、可并行的子任务，depends_on 留空 []。
4. priority 取 1-5，1 为最高优先级。
5. 每个 task 必须是单行字符串，禁止包含换行符。
6. 高风险操作（退款、批量补发、改价、上下架、改库存）无需在此判断，
   由系统的审批护栏处理；你只需如实描述任务。
7. 若目标跨越多个领域，拆成多个子任务并标注依赖。

示例1：
用户："分析某品类在东南亚市场的选品机会，输出选品建议和竞品分析"
输出：
[
  {"id":"t1","task":"调研目标品类在东南亚市场的需求与竞品价格分布","assigned_to":"selection","depends_on":[],"priority":1}
]

示例2：
用户："处理本周物流异常，协调补发或退款"
输出：
[
  {"id":"t1","task":"查询本周异常订单并分类异常原因","assigned_to":"order_logistics","depends_on":[],"priority":1},
  {"id":"t2","task":"依据异常原因决策补发或退款并执行（含审批）","assigned_to":"order_logistics","depends_on":["t1"],"priority":2},
  {"id":"t3","task":"为受影响订单生成客服通知草稿","assigned_to":"customer_service","depends_on":["t2"],"priority":3}
]

示例3：
用户："根据库存和销售数据动态调整商品定价，并汇总本周运营周报"
输出：
[
  {"id":"t1","task":"读取库存与近7天销量，计算各 SKU 建议售价","assigned_to":"pricing","depends_on":[],"priority":1},
  {"id":"t2","task":"汇总本周运营数据并生成周报，推送给运营负责人","assigned_to":"weekly_report","depends_on":["t1"],"priority":2}
]

只输出 JSON 数组，不要输出任何解释或 Markdown 代码块。
"""

# 目标关键词 → 领域（用于 LLM 失败时的兜底规划）
_KEYWORD_ROUTES = [
    (["选品", "竞品", "市场", "利润", "selection", "product"], "selection"),
    (["物流", "订单", "补发", "退款", "发货", "logistics", "order"], "order_logistics"),
    (["客服", "工单", "回复", "多语言", "ticket", "service"], "customer_service"),
    (["定价", "价格", "调价", "pricing", "price"], "pricing"),
    (["周报", "报表", "汇总", "report", "weekly"], "weekly_report"),
]


def _fallback_plan(goal: str) -> list[dict]:
    """LLM 不可用时的兜底：按关键词识别领域；默认给选品+周报两步。"""
    g = (goal or "").lower()
    areas: list[str] = []
    for kws, area in _KEYWORD_ROUTES:
        if any(k in g for k in kws):
            areas.append(area)
    if not areas:
        areas = ["selection", "weekly_report"]

    plan = []
    for i, area in enumerate(areas):
        plan.append(
            {
                "id": f"t{i + 1}",
                "task": f"处理运营目标[{area}]：{goal}",
                "assigned_to": area,
                "depends_on": [] if i == 0 else [f"t{i}"],
                "priority": i + 1,
            }
        )
    return plan


def _normalize_plan(plan: list) -> list[dict]:
    """规范化：补齐字段、校验 assigned_to、剔除非法依赖。"""
    valid_agents = {"selection", "order_logistics", "customer_service", "pricing", "weekly_report"}
    ids = {str(t.get("id")) for t in plan if isinstance(t, dict)}
    out: list[dict] = []
    for i, t in enumerate(plan):
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or f"t{i + 1}")
        agent = str(t.get("assigned_to", "")).strip()
        if agent not in valid_agents:
            agent = "selection"
        deps = [str(d) for d in (t.get("depends_on") or []) if str(d) in ids and str(d) != tid]
        out.append(
            {
                "id": tid,
                "task": str(t.get("task", "")).replace("\n", " ").strip(),
                "assigned_to": agent,
                "depends_on": deps,
                "priority": int(t.get("priority", i + 1) or (i + 1)),
            }
        )
    return out or _fallback_plan("")


async def planner_node(state: AgentState) -> dict:
    llm = await get_llm(state["user_id"])

    response = await llm.ainvoke(
        [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": f"运营目标：{state['goal']}"},
        ]
    )

    tokens_used = getattr(response, "usage_metadata", {}).get("total_tokens", 0)

    plan: list[dict]
    try:
        raw = response.content.strip()
        cleaned = re.sub(r"```json|```", "", raw).strip()
        parsed = json.loads(cleaned)
        plan = _normalize_plan(parsed if isinstance(parsed, list) else [])
    except json.JSONDecodeError:
        log.warning("plan_parse_failed", fallback=True)
        plan = _fallback_plan(state["goal"])

    log.info("plan_created", tasks=len(plan))

    return {
        "plan": plan,
        "tokens_used": (state.get("tokens_used") or 0) + tokens_used,
        "messages": [AIMessage(content=f"已生成 {len(plan)} 个运营子任务")],
    }
