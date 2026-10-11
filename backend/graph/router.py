# backend/graph/router.py
# ──────────────────────────────────────────────────────────────
#  运营路由：把运营目标分类到领域任务类型
#  classify_domain 返回 task_type：selection / logistics / cs / pricing /
#  weekly_report / mixed，供 POST /tasks 自动判定任务类型。
# ──────────────────────────────────────────────────────────────
# 领域关键词 → 任务类型
_DOMAIN_KEYWORDS = [
    (["选品", "竞品", "市场", "利润", "selection", "product", "选款"], "selection"),
    (
        ["物流", "订单", "补发", "退款", "发货", "异常件", "logistics", "order", "shipment"],
        "logistics",
    ),
    (["客服", "工单", "回复", "多语言", "投诉", "ticket", "service", "cs"], "cs"),
    (["定价", "价格", "调价", "pricing", "price"], "pricing"),
    (["周报", "报表", "汇总", "report", "weekly", "运营数据"], "weekly_report"),
]


def classify_domain(goal: str) -> str:
    """把运营目标分类为领域 task_type（命中多个领域 → mixed）。"""
    g = (goal or "").lower()
    hits = [area for kws, area in _DOMAIN_KEYWORDS if any(k in g for k in kws)]
    if len(hits) == 1:
        return hits[0]
    return "mixed"
