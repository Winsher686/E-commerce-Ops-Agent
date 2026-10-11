# backend/tools/domain/market_data.py
# ──────────────────────────────────────────────────────────────
#  工具：市场数据查询
#  用途：选品分析 Agent 获取目标市场的品类需求、增长与子类目结构。
#  数据源：mock 模式读 sample_data/market_data.json；真实模式调 MARKET_DATA_API。
#  降级：数据缺失时返回零值结构，并在 notes 标注“数据缺失”。
# ──────────────────────────────────────────────────────────────
from config import get_settings
from tools.domain._common import http_get, load_sample
from tools.domain.schemas import MarketDataInput, MarketDataOutput, SubCategoryStat

settings = get_settings()


def _match(records: list[dict], market: str, category: str) -> list[dict]:
    m = (market or "").upper()
    c = (category or "").lower()
    hits = [
        r
        for r in records
        if (r.get("market", "").upper() == m) and (c in r.get("category", "").lower())
    ]
    return hits


async def run(inp: MarketDataInput) -> MarketDataOutput:
    if settings.mock_enabled:
        records = load_sample("market_data") or []
        hits = _match(records, inp.market, inp.category)

        if not hits:
            return MarketDataOutput(
                task_id=inp.task_id,
                market=inp.market,
                category=inp.category,
                demand_index=0.0,
                trend="stable",
                notes=f"Mock 数据中未找到 {inp.market}/{inp.category}，请检查品类命名。",
                source="mock(empty)",
            )

        # 合并同品类多个子类目
        subs: list[SubCategoryStat] = []
        for r in hits:
            subs.append(
                SubCategoryStat(
                    name=r.get("subcategory", r.get("category", "")),
                    demand_index=float(r.get("demand_index", 0)),
                    growth_pct=float(r.get("growth_pct", 0)),
                    avg_price_usd=float(r.get("avg_price_usd", 0)),
                )
            )

        demand = round(sum(s.demand_index for s in subs) / len(subs), 2)
        avg_growth = sum(s.growth_pct for s in subs) / len(subs)
        trend = "rising" if avg_growth > 5 else ("declining" if avg_growth < -5 else "stable")

        return MarketDataOutput(
            task_id=inp.task_id,
            market=inp.market,
            category=inp.category,
            demand_index=demand,
            trend=trend,
            seasonality=hits[0].get("seasonality", ""),
            top_subcategories=sorted(subs, key=lambda s: -s.demand_index)[:5],
            notes=hits[0].get("notes", ""),
            source="mock",
        )

    # ── 真实 API 适配器 ───────────────────────────────────────
    data = await http_get(
        f"{settings.order_api_base or 'https://api.marketdata.example.com'}/v1/market",
        params={"market": inp.market, "category": inp.category, "kw": ",".join(inp.keywords)},
        headers={"Authorization": f"Bearer {settings.market_data_api_key}"},
    )
    subs = [SubCategoryStat(**s) for s in data.get("subcategories", [])]
    return MarketDataOutput(
        task_id=inp.task_id,
        market=inp.market,
        category=inp.category,
        demand_index=float(data.get("demand_index", 0)),
        trend=data.get("trend", "stable"),
        seasonality=data.get("seasonality", ""),
        top_subcategories=subs,
        notes=data.get("notes", ""),
        source="api",
    )
