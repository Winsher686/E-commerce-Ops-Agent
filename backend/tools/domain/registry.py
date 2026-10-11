# backend/tools/domain/registry.py
# ──────────────────────────────────────────────────────────────
#  跨境电商运营工具注册表
#  统一登记每个工具的：输入/输出 Schema、调用入口、是否需要审批、审批类型。
#  Agent 通过 registry.call(name, payload) 调用工具，由包装器统一处理
#  超时 / 重试 / 降级。
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Type

from pydantic import BaseModel

from tools.domain import (
    competitor_price,
    db_query,
    fx_rate,
    inventory_api,
    logistics_api,
    notify,
    order_api,
    pricing_engine,
    profit_calculator,
    refund_api,
    ticket_api,
    translation,
    market_data,
)
from tools.domain import schemas as S
from tools.domain._common import run_tool


@dataclass
class ToolSpec:
    name: str
    description: str
    module: Any
    func_name: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    requires_approval: bool = False
    approval_type: str = ""          # price_change / refund / bulk_reship / inventory_update / listing_toggle
    tags: list[str] = field(default_factory=list)


TOOLS: dict[str, ToolSpec] = {
    # ── 选品分析 ──────────────────────────────────────────────
    "market_data": ToolSpec(
        "market_data", "查询目标市场的品类需求与子类目结构",
        market_data, "run", S.MarketDataInput, S.MarketDataOutput,
        tags=["selection"],
    ),
    "competitor_price": ToolSpec(
        "competitor_price", "抓取竞品在各平台的售价分布",
        competitor_price, "run", S.CompetitorPriceInput, S.CompetitorPriceOutput,
        tags=["selection", "pricing"],
    ),
    "fx_rate": ToolSpec(
        "fx_rate", "查询货币汇率",
        fx_rate, "run", S.FXRateInput, S.FXRateOutput,
        tags=["selection", "pricing"],
    ),
    "profit_calculator": ToolSpec(
        "profit_calculator", "估算单位毛利与盈亏平衡售价（本地计算）",
        profit_calculator, "run", S.ProfitCalcInput, S.ProfitCalcOutput,
        tags=["selection", "pricing"],
    ),

    # ── 订单与物流 ────────────────────────────────────────────
    "order_query": ToolSpec(
        "order_query", "查询订单及状态",
        order_api, "run", S.OrderQueryInput, S.OrderQueryOutput,
        tags=["logistics"],
    ),
    "logistics_track": ToolSpec(
        "logistics_track", "追踪物流轨迹并识别异常",
        logistics_api, "run", S.LogisticsTrackInput, S.LogisticsTrackOutput,
        tags=["logistics"],
    ),
    "inventory_query": ToolSpec(
        "inventory_query", "查询库存与安全水位",
        inventory_api, "run", S.InventoryQueryInput, S.InventoryQueryOutput,
        tags=["logistics", "pricing"],
    ),
    "inventory_update": ToolSpec(
        "inventory_update", "调整库存数量（高风险）",
        inventory_api, "update", S.InventoryUpdateInput, S.InventoryUpdateOutput,
        requires_approval=True, approval_type="inventory_update", tags=["logistics", "pricing"],
    ),
    "refund": ToolSpec(
        "refund", "执行退款（高风险）",
        refund_api, "run", S.RefundInput, S.RefundOutput,
        requires_approval=True, approval_type="refund", tags=["logistics"],
    ),

    # ── 多语言客服 ────────────────────────────────────────────
    "ticket_list": ToolSpec(
        "ticket_list", "拉取客服工单列表",
        ticket_api, "list_tickets", S.TicketListInput, S.TicketListOutput,
        tags=["cs"],
    ),
    "ticket_classify": ToolSpec(
        "ticket_classify", "对工单分类并做情感分析",
        ticket_api, "classify", S.TicketClassifyInput, S.TicketClassifyOutput,
        tags=["cs"],
    ),
    "translation": ToolSpec(
        "translation", "多语言翻译",
        translation, "run", S.TranslateInput, S.TranslateOutput,
        tags=["cs"],
    ),

    # ── 定价 ──────────────────────────────────────────────────
    "pricing_engine": ToolSpec(
        "pricing_engine", "定价规则引擎，输出建议售价与变动幅度",
        pricing_engine, "run", S.PricingRuleInput, S.PricingRuleOutput,
        tags=["pricing"],
    ),

    # ── 周报 ──────────────────────────────────────────────────
    "db_query": ToolSpec(
        "db_query", "受控只读数据库查询",
        db_query, "run", S.DBQueryInput, S.DBQueryOutput,
        tags=["report", "cs"],
    ),
    "notify": ToolSpec(
        "notify", "通过邮件 / Webhook / WebSocket 推送通知",
        notify, "run", S.NotifyInput, S.NotifyOutput,
        tags=["report"],
    ),
}


def get(name: str) -> ToolSpec:
    if name not in TOOLS:
        raise KeyError(f"未知工具：{name}")
    return TOOLS[name]


def requires_approval(name: str) -> bool:
    return TOOLS.get(name, ToolSpec("", "", None, "", BaseModel, BaseModel)).requires_approval


async def call(name: str, payload: dict[str, Any]) -> tuple[Optional[BaseModel], Optional[str]]:
    """
    调用工具并返回 (输出模型, 错误信息)。
    统一走 run_tool 包装器（超时 + 重试 + 降级）。
    """
    spec = get(name)
    inp = spec.input_schema(**payload)
    fn = getattr(spec.module, spec.func_name)

    fallback = None
    result, err = await run_tool(spec.name, lambda: fn(inp), fallback=fallback)
    return result, err
