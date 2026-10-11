# backend/tools/domain/competitor_price.py
# ──────────────────────────────────────────────────────────────
#  工具：竞品价格抓取
#  用途：选品 / 定价 Agent 获取竞品在各平台的售价分布。
#  数据源：mock 读 sample_data/competitor_prices.json；真实模式调 COMPETITOR_API。
#  降级：无数据时返回空分布，Agent 应改用保底定价策略。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get, load_sample
from tools.domain.schemas import (
    CompetitorPriceInput,
    CompetitorPriceItem,
    CompetitorPriceOutput,
)

settings = get_settings()


async def run(inp: CompetitorPriceInput) -> CompetitorPriceOutput:
    if settings.mock_enabled:
        records = load_sample("competitor_prices") or []
        hits = [r for r in records if r.get("sku") == inp.sku]

        items = [
            CompetitorPriceItem(
                platform=r.get("platform", ""),
                seller=r.get("seller", ""),
                price=float(r.get("price", 0)),
                currency=r.get("currency", "USD"),
                rating=float(r.get("rating", 0)),
                sold=int(r.get("sold", 0)),
                url=r.get("url", ""),
            )
            for r in hits
        ]
        prices = [i.price for i in items if i.price > 0]
        return CompetitorPriceOutput(
            task_id=inp.task_id, sku=inp.sku, market=inp.market,
            items=items,
            min_price=min(prices) if prices else 0.0,
            max_price=max(prices) if prices else 0.0,
            avg_price=round(sum(prices) / len(prices), 2) if prices else 0.0,
            currency="USD",
            source="mock",
        )

    # ── 真实 API 适配器 ───────────────────────────────────────
    data = await http_get(
        "https://api.competitor.example.com/v1/prices",
        params={"sku": inp.sku, "market": inp.market, "platforms": ",".join(inp.platforms)},
        headers={"Authorization": f"Bearer {settings.competitor_api_key}"},
    )
    items = [CompetitorPriceItem(**i) for i in data.get("items", [])]
    prices = [i.price for i in items if i.price > 0]
    return CompetitorPriceOutput(
        task_id=inp.task_id, sku=inp.sku, market=inp.market, items=items,
        min_price=min(prices) if prices else 0.0,
        max_price=max(prices) if prices else 0.0,
        avg_price=round(sum(prices) / len(prices), 2) if prices else 0.0,
        source="api",
    )
