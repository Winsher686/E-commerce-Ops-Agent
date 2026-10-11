# backend/tests/test_approval_guardrail.py
"""人工审批护栏（HITL）测试：审批矩阵、操作键与 gate 分支。"""

import pytest

from graph.approval import (
    HAS_INTERRUPT,
    build_request,
    decide_level,
    gate,
    level_for_price_change,
    level_for_refund,
    level_for_reship,
    operation_key,
)


def test_refund_matrix_levels():
    assert level_for_refund(50)[0] == "auto"
    assert level_for_refund(300)[0] == "supervisor"
    assert level_for_refund(1200)[0] == "manager"


def test_refund_matrix_boundaries():
    # 边界：≤100 自动；100~500 主管；>500 经理
    assert level_for_refund(100)[0] == "auto"
    assert level_for_refund(100.01)[0] == "supervisor"
    assert level_for_refund(500)[0] == "supervisor"
    assert level_for_refund(500.01)[0] == "manager"


def test_reship_matrix_levels():
    assert level_for_reship(50, 1)[0] == "auto"  # 金额与件数都在阈值内
    assert level_for_reship(250, 1)[0] == "supervisor"  # 超金额
    assert level_for_reship(50, 5)[0] == "supervisor"  # 批量件数达到阈值


def test_price_change_matrix_boundaries():
    assert level_for_price_change(5)[0] == "auto"
    assert level_for_price_change(5.01)[0] == "supervisor"
    assert level_for_price_change(15)[0] == "supervisor"
    assert level_for_price_change(15.01)[0] == "manager"


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
    rec = build_request(
        "order_logistics", "refund", {"order_id": "O-9", "amount_usd": 260}, amount_usd=260
    )
    assert rec["required_level"] == "supervisor"
    assert rec["status"] == "pending"


@pytest.mark.asyncio
async def test_gate_auto_for_small_refund():
    out = await gate(
        agent="order_logistics",
        operation_type="refund",
        payload={"order_id": "O-1", "amount_usd": 30},
        amount_usd=30,
    )
    assert out["action"] == "auto"


@pytest.mark.asyncio
async def test_gate_approved_via_preapproved_list():
    key = operation_key("refund", {"order_id": "O-2"})
    out = await gate(
        agent="order_logistics",
        operation_type="refund",
        payload={"order_id": "O-2", "amount_usd": 300},
        amount_usd=300,
        preapproved=[key],
    )
    assert out["action"] == "approved"
    assert out["approval"]["decided_by"] == "human(preapproved)"


@pytest.mark.asyncio
async def test_gate_pending_when_over_threshold_not_preapproved():
    """超阈值且未预批准：无 interrupt 的版本应降级为 pending。"""
    out = await gate(
        agent="order_logistics",
        operation_type="refund",
        payload={"order_id": "O-3", "amount_usd": 300},
        amount_usd=300,
    )
    assert out["approval"]["required_level"] == "supervisor"
    if not HAS_INTERRUPT:
        assert out["action"] == "pending"
        assert out["approval"]["status"] == "pending"
