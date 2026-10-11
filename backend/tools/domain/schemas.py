# backend/tools/domain/schemas.py
# ──────────────────────────────────────────────────────────────
#  电商运营中台 —— 工具输入/输出 Schema（Pydantic）
#  所有领域工具的 IO 均以此为准，Agent 通过 schema 与工具解耦。
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolBase(BaseModel):
    """工具基类：统一携带任务上下文，便于记录与可观测。"""

    task_id: str | None = Field(default=None, description="所属运营任务 ID")


# ══════════════════════════════════════════════════════════════
#  1. 市场数据
# ══════════════════════════════════════════════════════════════
class MarketDataInput(ToolBase):
    market: str = Field(default="SEA", description="目标市场，如 SEA / TH / ID / VN")
    category: str = Field(..., description="品类，如 phone_accessories")
    keywords: list[str] = Field(default_factory=list, description="相关关键词")


class SubCategoryStat(BaseModel):
    name: str
    demand_index: float = Field(description="需求指数 0-100")
    growth_pct: float = Field(default=0.0, description="同比增长 %")
    avg_price_usd: float = 0.0


class MarketDataOutput(ToolBase):
    market: str
    category: str
    demand_index: float
    trend: Literal["rising", "stable", "declining"] = "stable"
    seasonality: str = ""
    top_subcategories: list[SubCategoryStat] = Field(default_factory=list)
    notes: str = ""
    source: str = "mock"


# ══════════════════════════════════════════════════════════════
#  2. 竞品价格
# ══════════════════════════════════════════════════════════════
class CompetitorPriceInput(ToolBase):
    sku: str
    market: str = "SEA"
    platforms: list[str] = Field(default_factory=lambda: ["shopee", "lazada"])


class CompetitorPriceItem(BaseModel):
    platform: str
    seller: str = ""
    price: float
    currency: str = "USD"
    rating: float = 0.0
    sold: int = 0
    url: str = ""


class CompetitorPriceOutput(ToolBase):
    sku: str
    market: str
    items: list[CompetitorPriceItem] = Field(default_factory=list)
    min_price: float = 0.0
    max_price: float = 0.0
    avg_price: float = 0.0
    currency: str = "USD"
    source: str = "mock"


# ══════════════════════════════════════════════════════════════
#  3. 汇率
# ══════════════════════════════════════════════════════════════
class FXRateInput(ToolBase):
    base: str = "USD"
    quote: str = "CNY"


class FXRateOutput(ToolBase):
    base: str
    quote: str
    rate: float
    as_of: str = ""
    source: str = "mock"


# ══════════════════════════════════════════════════════════════
#  4. 利润计算器（本地纯函数，无需外部系统）
# ══════════════════════════════════════════════════════════════
class ProfitCalcInput(ToolBase):
    cost_usd: float = Field(description="采购成本（USD）")
    price_usd: float = Field(description="售价（USD）")
    shipping_rate: float = Field(default=0.08, description="运费占售价比例")
    commission_rate: float = Field(default=0.05, description="平台佣金比例")
    ad_rate: float = Field(default=0.10, description="广告费比例")
    units: int = 1


class ProfitCalcOutput(ToolBase):
    revenue_usd: float
    cost_usd: float
    shipping_usd: float
    commission_usd: float
    ad_usd: float
    profit_usd: float
    margin_pct: float
    breakeven_price_usd: float


# ══════════════════════════════════════════════════════════════
#  5. 订单
# ══════════════════════════════════════════════════════════════
class OrderQueryInput(ToolBase):
    order_ids: list[str] = Field(default_factory=list)
    platform: str | None = None
    status: str | None = None
    limit: int = 50


class Order(BaseModel):
    order_id: str
    platform: str = ""
    sku: str = ""
    qty: int = 1
    amount_usd: float = 0.0
    currency: str = "USD"
    status: str = ""
    buyer: str = ""
    country: str = ""
    created_at: str = ""


class OrderQueryOutput(ToolBase):
    orders: list[Order] = Field(default_factory=list)
    count: int = 0
    source: str = "mock"


# ══════════════════════════════════════════════════════════════
#  6. 物流
# ══════════════════════════════════════════════════════════════
class LogisticsTrackInput(ToolBase):
    order_id: str | None = None
    tracking_no: str | None = None


class LogisticsEvent(BaseModel):
    time: str
    status: str
    location: str = ""
    detail: str = ""


class LogisticsTrackOutput(ToolBase):
    order_id: str = ""
    tracking_no: str = ""
    carrier: str = ""
    status: str = ""
    events: list[LogisticsEvent] = Field(default_factory=list)
    is_exception: bool = False
    exception_type: str = ""
    source: str = "mock"


# ══════════════════════════════════════════════════════════════
#  7. 库存
# ══════════════════════════════════════════════════════════════
class InventoryQueryInput(ToolBase):
    sku: str | None = None
    sku_list: list[str] = Field(default_factory=list)
    warehouse: str | None = None


class InventoryItem(BaseModel):
    sku: str
    warehouse: str = ""
    quantity: int = 0
    safety_stock: int = 0
    available: bool = True


class InventoryQueryOutput(ToolBase):
    items: list[InventoryItem] = Field(default_factory=list)
    total_quantity: int = 0
    source: str = "mock"


class InventoryUpdateInput(ToolBase):
    sku: str
    quantity: int
    warehouse: str = "default"
    reason: str = ""


class InventoryUpdateOutput(ToolBase):
    success: bool
    sku: str
    new_quantity: int
    message: str = ""


# ══════════════════════════════════════════════════════════════
#  8. 退款（高风险 —— 需审批）
# ══════════════════════════════════════════════════════════════
class RefundInput(ToolBase):
    order_id: str
    amount_usd: float
    reason: str = ""
    method: Literal["original", "wallet", "coupon"] = "original"


class RefundOutput(ToolBase):
    success: bool
    refund_id: str = ""
    order_id: str = ""
    amount_usd: float = 0.0
    status: str = ""
    message: str = ""


# ══════════════════════════════════════════════════════════════
#  9. 翻译
# ══════════════════════════════════════════════════════════════
class TranslateInput(ToolBase):
    text: str
    target_lang: str = "en"
    source_lang: str = "auto"


class TranslateOutput(ToolBase):
    text: str
    source_lang: str = "auto"
    target_lang: str = "en"
    engine: str = "mock"


# ══════════════════════════════════════════════════════════════
#  10. 客服工单
# ══════════════════════════════════════════════════════════════
class TicketListInput(ToolBase):
    status: str | None = None
    category: str | None = None
    language: str | None = None
    limit: int = 50


class SupportTicket(BaseModel):
    ticket_no: str
    platform: str = ""
    language: str = "en"
    customer: str = ""
    message: str = ""
    category: str = ""
    sentiment: str = "neutral"
    status: str = "open"
    created_at: str = ""


class TicketListOutput(ToolBase):
    tickets: list[SupportTicket] = Field(default_factory=list)
    count: int = 0
    source: str = "mock"


class TicketClassifyInput(ToolBase):
    ticket_no: str
    message: str


class TicketClassifyOutput(ToolBase):
    ticket_no: str
    category: str
    sentiment: str
    confidence: float = 0.0
    recommended_action: str = ""


# ══════════════════════════════════════════════════════════════
#  11. 定价规则引擎（本地规则 + 竞品/库存输入）
# ══════════════════════════════════════════════════════════════
class PricingRuleInput(ToolBase):
    sku: str
    cost_usd: float
    current_price_usd: float
    competitor_avg_usd: float = 0.0
    inventory_qty: int = 0
    sales_7d: int = 0
    target_margin_pct: float = 30.0
    min_change_pct: float = 1.0
    max_change_pct: float = 25.0


class PricingRuleOutput(ToolBase):
    sku: str
    suggested_price_usd: float
    change_pct: float
    rationale: str = ""
    within_limits: bool = True
    source: str = "rules"


# ══════════════════════════════════════════════════════════════
#  12. 通知 / 推送
# ══════════════════════════════════════════════════════════════
class NotifyInput(ToolBase):
    channel: Literal["email", "webhook", "websocket"] = "email"
    recipient: str = ""
    subject: str = ""
    body: str = ""


class NotifyOutput(ToolBase):
    success: bool
    channel: str
    message_id: str = ""
    detail: str = ""


# ══════════════════════════════════════════════════════════════
#  13. 数据库查询（受控只读）
# ══════════════════════════════════════════════════════════════
class DBQueryInput(ToolBase):
    sql: str | None = Field(default=None, description="只读 SELECT 语句")
    table: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 100


class DBQueryOutput(ToolBase):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0
    source: str = "postgres"


# ══════════════════════════════════════════════════════════════
#  审批（HITL）
# ══════════════════════════════════════════════════════════════
ApprovalLevel = Literal["auto", "supervisor", "manager"]


class ApprovalRequest(BaseModel):
    task_id: str | None = None
    subtask_id: str | None = None
    agent: str
    operation_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    amount_usd: float = 0.0
    required_level: ApprovalLevel = "supervisor"
    reason: str = ""


class ApprovalRecord(ApprovalRequest):
    id: str
    status: Literal["pending", "approved", "rejected", "cancelled"] = "pending"
    decided_by: str | None = None
    decided_at: datetime | None = None
    requested_at: datetime | None = None
