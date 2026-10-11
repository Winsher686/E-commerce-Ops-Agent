# backend/tools/domain/profit_calculator.py
# ──────────────────────────────────────────────────────────────
#  工具：利润计算器（本地纯函数，无外部依赖）
#  用途：选品 / 定价 Agent 估算单位毛利与盈亏平衡售价。
#  成本构成：采购成本 + 运费(售价*shipping_rate) + 平台佣金 + 广告费。
# ──────────────────────────────────────────────────────────────
from tools.domain.schemas import ProfitCalcInput, ProfitCalcOutput


async def run(inp: ProfitCalcInput) -> ProfitCalcOutput:
    units = max(1, inp.units)
    revenue = inp.price_usd * units
    cost = inp.cost_usd * units
    shipping = inp.price_usd * inp.shipping_rate * units
    commission = inp.price_usd * inp.commission_rate * units
    ad = inp.price_usd * inp.ad_rate * units

    profit = revenue - cost - shipping - commission - ad
    margin_pct = (profit / revenue * 100) if revenue > 0 else 0.0

    # 盈亏平衡：售价需覆盖成本并按各费率摊平
    rate_sum = inp.shipping_rate + inp.commission_rate + inp.ad_rate
    breakeven = inp.cost_usd / (1 - rate_sum) if rate_sum < 1 else inp.cost_usd

    return ProfitCalcOutput(
        task_id=inp.task_id,
        revenue_usd=round(revenue, 2),
        cost_usd=round(cost, 2),
        shipping_usd=round(shipping, 2),
        commission_usd=round(commission, 2),
        ad_usd=round(ad, 2),
        profit_usd=round(profit, 2),
        margin_pct=round(margin_pct, 2),
        breakeven_price_usd=round(breakeven, 2),
    )
