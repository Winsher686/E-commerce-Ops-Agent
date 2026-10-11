# backend/tools/domain/order_api.py
# ──────────────────────────────────────────────────────────────
#  工具：订单系统查询
#  用途：订单与物流 Agent 拉取订单及状态，筛选异常订单。
#  数据源：mock 读 sample_data/orders.json；真实模式调 ORDER_API_BASE。
#  降级：查询失败返回空列表，Agent 应转人工并记录。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get, load_sample
from tools.domain.schemas import Order, OrderQueryInput, OrderQueryOutput

settings = get_settings()


async def run(inp: OrderQueryInput) -> OrderQueryOutput:
    if settings.mock_enabled:
        records = load_sample("orders") or []
        rows = records

        if inp.order_ids:
            ids = set(inp.order_ids)
            rows = [r for r in rows if r.get("order_id") in ids]
        if inp.platform:
            rows = [r for r in rows if r.get("platform") == inp.platform]
        if inp.status:
            rows = [r for r in rows if r.get("status") == inp.status]

        orders = [Order(**{k: r.get(k) for k in Order.model_fields}) for r in rows[: inp.limit]]
        return OrderQueryOutput(
            task_id=inp.task_id, orders=orders, count=len(orders), source="mock"
        )

    # ── 真实 API 适配器 ───────────────────────────────────────
    data = await http_get(
        f"{settings.order_api_base}/v1/orders",
        params={
            "ids": ",".join(inp.order_ids),
            "platform": inp.platform,
            "status": inp.status,
            "limit": inp.limit,
        },
        headers={"Authorization": f"Bearer {settings.order_api_key}"},
    )
    orders = [Order(**o) for o in data.get("orders", [])]
    return OrderQueryOutput(task_id=inp.task_id, orders=orders, count=len(orders), source="api")
