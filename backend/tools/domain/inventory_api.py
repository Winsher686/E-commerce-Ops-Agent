# backend/tools/domain/inventory_api.py
# ──────────────────────────────────────────────────────────────
#  工具：库存查询 / 更新
#  用途：定价 Agent 判断库存压力；订单 Agent 判断能否补发。
#  数据源：mock 读 sample_data/inventory.json；真实模式调 INVENTORY_API_BASE。
#  权限：库存更新（adjust）为高风险操作 —— 需人工审批。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get, load_sample
from tools.domain.schemas import (
    InventoryItem,
    InventoryQueryInput,
    InventoryQueryOutput,
    InventoryUpdateInput,
    InventoryUpdateOutput,
)

settings = get_settings()


async def run(inp: InventoryQueryInput) -> InventoryQueryOutput:
    if settings.mock_enabled:
        records = load_sample("inventory") or []
        rows = records
        if inp.sku:
            rows = [r for r in rows if r.get("sku") == inp.sku]
        if inp.sku_list:
            s = set(inp.sku_list)
            rows = [r for r in rows if r.get("sku") in s]
        if inp.warehouse:
            rows = [r for r in rows if r.get("warehouse") == inp.warehouse]

        items = [InventoryItem(**{k: r.get(k) for k in InventoryItem.model_fields}) for r in rows]
        for it in items:
            it.available = it.quantity > 0
        return InventoryQueryOutput(
            task_id=inp.task_id, items=items,
            total_quantity=sum(i.quantity for i in items), source="mock",
        )

    # ── 真实 API 适配器 ───────────────────────────────────────
    data = await http_get(
        f"{settings.inventory_api_base}/v1/inventory",
        params={"sku": inp.sku, "warehouse": inp.warehouse},
        headers={"Authorization": f"Bearer {settings.inventory_api_key}"},
    )
    items = [InventoryItem(**i) for i in data.get("items", [])]
    return InventoryQueryOutput(
        task_id=inp.task_id, items=items,
        total_quantity=sum(i.quantity for i in items), source="api",
    )


async def update(inp: InventoryUpdateInput) -> InventoryUpdateOutput:
    """
    库存调整（高风险，调用前必须通过审批护栏）。
    mock 模式仅回显结果，不落库；真实模式 PUT 到库存系统。
    """
    if settings.mock_enabled:
        records = load_sample("inventory") or []
        base_qty = 0
        for r in records:
            if r.get("sku") == inp.sku:
                base_qty = int(r.get("quantity", 0))
                break
        return InventoryUpdateOutput(
            task_id=inp.task_id, success=True, sku=inp.sku,
            new_quantity=inp.quantity, message=f"mock 更新成功（原 {base_qty} → {inp.quantity}）",
        )

    data = await http_get(
        f"{settings.inventory_api_base}/v1/inventory/update",
        params={"sku": inp.sku, "quantity": inp.quantity, "warehouse": inp.warehouse},
        headers={"Authorization": f"Bearer {settings.inventory_api_key}"},
    )
    return InventoryUpdateOutput(
        task_id=inp.task_id, success=bool(data.get("success", True)),
        sku=inp.sku, new_quantity=int(data.get("new_quantity", inp.quantity)),
        message=data.get("message", ""),
    )
