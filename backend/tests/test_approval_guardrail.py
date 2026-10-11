# backend/tests/test_approval_guardrail.py
"""人工审批护栏（HITL）测试：审批矩阵、操作键与 gate 分支。"""
import pytest

from graph.approval import (
    build_request,
    decide_level,
    gate,
    level_for_price_change,
    level_for_refund,
    operation_key,
)


def test_refund_matrix_levels():
    assert level_for_refund(50)[0] == "auto"
    assert level_for_refund(300)[0] == "supervisor"
    assert level_for_refund(1200)[0] == "manager"


def test_price_change_matrix_levels():
    assert level_for_price_change(3)[0] == "auto"
    assert level_for_price_change(-10)[0] == "supervisor"
    assert level_for_price_change(30)[0] == "manager"


def test_decide_level_for_high_risk_ops():
    assert decide_level("inventory_update", {})[0] == "supervisor"
    assert decide_level("listing_toggle", {})[0] == "supervisor"


def test_operation_key_uses_business_identifier():
    assert operation_key("refund", {"order_id": "O-1001"}) == "refund:O-1001"
    assert operation_key("price_change", {"sku": "SKU-A"}) == "price_change:SKU-A"


def test_build_request_sets_required_level():
    rec = build_request("order_logistics", "refund",
                        {"order_id": "O-9", "amount_usd": 260}, amount_usd=260)
    assert rec["required_level"] == "supervisor"
    assert rec["status"] == "pending"


@pytest.mark.asyncio
async def test_gate_auto_for_small_refund():
    out = await gate(agent="order_logistics", operation_type="refund",
                     payload={"order_id": "O-1", "amount_usd": 30}, amount_usd=30)
    assert out["action"] == "auto"


@pytest.mark.asyncio
async def test_gate_approved_via_preapproved_list():
    key = operation_key("refund", {"order_id": "O-2"})
    out = await gate(agent="order_logistics", operation_type="refund",
                     payload={"order_id": "O-2", "amount_usd": 300}, amount_usd=300,
                     preapproved=[key])
    assert out["action"] == "approved"
    assert out["approval"]["decided_by"] == "human(preapproved)"
