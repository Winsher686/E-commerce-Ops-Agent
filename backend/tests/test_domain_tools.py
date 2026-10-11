# backend/tests/test_domain_tools.py
"""领域工具 IO Schema 与 mock 数据路径测试。"""

import pytest

from tools.domain import registry


@pytest.mark.asyncio
async def test_profit_calculator_returns_positive_margin():
    out, err = await registry.call("profit_calculator", {"cost_usd": 2, "price_usd": 10})
    assert err is None
    assert out.profit_usd > 0
    assert out.breakeven_price_usd > 2


@pytest.mark.asyncio
async def test_market_data_mock():
    out, err = await registry.call(
        "market_data", {"market": "SEA", "category": "phone_accessories"}
    )
    assert err is None
    assert out.demand_index > 0
    assert len(out.top_subcategories) >= 1


@pytest.mark.asyncio
async def test_competitor_price_mock():
    out, err = await registry.call(
        "competitor_price", {"sku": "SKU-PHONE-CASE-01", "market": "SEA"}
    )
    assert err is None
    assert out.avg_price > 0
    assert out.min_price <= out.avg_price <= out.max_price


@pytest.mark.asyncio
async def test_order_and_logistics_mock():
    orders, err = await registry.call("order_query", {})
    assert err is None
    assert orders.count >= 1
    first = orders.orders[0]
    track, err = await registry.call("logistics_track", {"order_id": first.order_id})
    assert err is None
    assert track.order_id == first.order_id


@pytest.mark.asyncio
async def test_refund_mock_success():
    out, err = await registry.call(
        "refund", {"order_id": "O-1001", "amount_usd": 12.9, "reason": "丢件"}
    )
    assert err is None
    assert out.success is True


@pytest.mark.asyncio
async def test_fx_and_ticket_classify():
    fx, _ = await registry.call("fx_rate", {"base": "USD", "quote": "THB"})
    assert fx.rate > 0
    cls, _ = await registry.call(
        "ticket_classify", {"ticket_no": "T-2001", "message": "我还没收到包裹"}
    )
    assert cls.category == "物流"


def test_registry_marks_high_risk_tools():
    assert registry.requires_approval("refund") is True
    assert registry.requires_approval("inventory_update") is True
    assert registry.requires_approval("market_data") is False


def test_unknown_tool_raises():
    with pytest.raises(KeyError):
        registry.get("does_not_exist")
