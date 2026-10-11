# backend/tools/domain/pricing_engine.py
# ──────────────────────────────────────────────────────────────
#  工具：定价规则引擎（本地规则，可解释）
#  用途：定价 Agent 根据成本/竞品/库存/销量输出建议售价。
#  规则：
#    - 保底价 = 成本 / (1 - 费率合计) * (1 + 目标利润率)
#    - 库存高企（>3x 安全水位）→ 下调促销；库存紧张 → 上调
#    - 竞品均价偏离 → 向竞品均价靠拢，但受 min/max 变动幅度约束
#  输出：建议价、变动幅度、依据、是否在允许区间内（供审批护栏判断）。
# ──────────────────────────────────────────────────────────────
from tools.domain.schemas import PricingRuleInput, PricingRuleOutput


async def run(inp: PricingRuleInput) -> PricingRuleOutput:
    rate_sum = 0.08 + 0.05 + 0.10  # 运费+佣金+广告（与默认费率保持一致）
    floor_price = inp.cost_usd / (1 - rate_sum) * (1 + inp.target_margin_pct / 100)

    price = inp.current_price_usd
    reasons: list[str] = []

    # 1) 库存因子
    if inp.inventory_qty >= 300:
        price *= 0.95
        reasons.append("库存高企，降价促清")
    elif inp.inventory_qty <= 20:
        price *= 1.05
        reasons.append("库存紧张，提价保利润")

    # 2) 竞品因子
    if inp.competitor_avg_usd > 0:
        if inp.current_price_usd > inp.competitor_avg_usd * 1.15:
            price = min(price, inp.competitor_avg_usd * 1.05)
            reasons.append("高于竞品均价 15%，向下靠拢")
        elif inp.current_price_usd < inp.competitor_avg_usd * 0.85:
            price = max(price, inp.competitor_avg_usd * 0.95)
            reasons.append("低于竞品均价 15%，向上修复")

    # 3) 销量因子
    if inp.sales_7d >= 50:
        price *= 1.02
        reasons.append("近 7 天热销，小幅上浮")

    # 4) 保底
    if price < floor_price:
        price = floor_price
        reasons.append("触发保底价，保证目标毛利")

    change_pct = (price - inp.current_price_usd) / inp.current_price_usd * 100 if inp.current_price_usd else 0.0

    # 5) 幅度约束
    within = True
    if abs(change_pct) > inp.max_change_pct:
        capped = inp.max_change_pct if change_pct > 0 else -inp.max_change_pct
        price = inp.current_price_usd * (1 + capped / 100)
        change_pct = capped
        within = False
        reasons.append(f"变动超过上限 {inp.max_change_pct}%，已做封顶")

    return PricingRuleOutput(
        task_id=inp.task_id,
        sku=inp.sku,
        suggested_price_usd=round(price, 2),
        change_pct=round(change_pct, 2),
        rationale="；".join(reasons) or "维持现价",
        within_limits=within,
        source="rules",
    )
