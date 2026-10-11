# backend/graph/router.py
# ──────────────────────────────────────────────────────────────
#  运营路由：把运营目标分类到领域任务类型 / 直达单 Agent
#  - classify_goal：兼容旧 /run 入口，返回路由字符串
#  - classify_domain：给 /tasks 使用，返回 task_type
# ──────────────────────────────────────────────────────────────
from typing import Literal

# 领域关键词 → 任务类型
_DOMAIN_KEYWORDS = [
    (["选品", "竞品", "市场", "利润", "selection", "product", "选款"], "selection"),
    (["物流", "订单", "补发", "退款", "发货", "异常件", "logistics", "order", "shipment"], "logistics"),
    (["客服", "工单", "回复", "多语言", "投诉", "ticket", "service", "cs"], "cs"),
    (["定价", "价格", "调价", "pricing", "price"], "pricing"),
    (["周报", "报表", "汇总", "report", "weekly", "运营数据"], "weekly_report"),
]

_SLASH = {
    "/planner": "planner_only",
    "/selection": "selection_only",
    "/logistics": "logistics_only",
    "/cs": "customer_service_only",
    "/pricing": "pricing_only",
    "/report": "weekly_report_only",
    "/rag": "rag",
}

GoalRoute = Literal[
    "planner_only", "selection_only", "logistics_only",
    "customer_service_only", "pricing_only", "weekly_report_only",
    "rag", "multi_agent",
]


def classify_domain(goal: str) -> str:
    """把运营目标分类为领域 task_type（可能命中多个 → mixed）。"""
    g = (goal or "").lower()
    hits = [area for kws, area in _DOMAIN_KEYWORDS if any(k in g for k in kws)]
    if not hits:
        return "mixed"
    if len(hits) == 1:
        return hits[0]
    return "mixed"


def classify_goal(
    goal: str,
    context_files: list[str] | None = None,
) -> str:
    goal_lower = goal.lower().strip()

    # ── 显式 slash 命令 ───────────────────────────────────────
    for prefix, route in _SLASH.items():
        if goal_lower.startswith(prefix):
            return route

    # ── 简单 RAG / 文件问答（客服知识库）──────────────────────
    simple_questions = ["what is", "find", "search", "tell me", "summarize",
                        "explain", "什么是", "查一下", "总结", "解释"]
    if context_files and any(goal_lower.startswith(q) for q in simple_questions) and len(goal.split()) < 15:
        return "rag"

    # ── 其余走完整多 Agent 编排 ────────────────────────────────
    return "multi_agent"
