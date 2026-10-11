# backend/tests/test_supervisor_dag.py
"""Supervisor 编排逻辑与 DAG 规划测试。"""

import pytest

from graph.agents.planner import _fallback_plan, _normalize_plan
from graph.supervisor import STUCK_LIMIT, supervisor_node


def test_fallback_plan_detects_selection():
    plan = _fallback_plan("分析东南亚市场的选品机会")
    assert len(plan) >= 1
    assert any(t["assigned_to"] == "selection" for t in plan)


def test_fallback_plan_defaults_when_unknown():
    plan = _fallback_plan("做点运营的事")
    assert {t["assigned_to"] for t in plan} <= {"selection", "weekly_report"}


def test_normalize_plan_fixes_invalid_agent_and_deps():
    raw = [
        {"id": "t1", "task": "调研", "assigned_to": "unknown_agent", "depends_on": ["t2"]},
        {"id": "t2", "task": "定价", "assigned_to": "pricing", "depends_on": []},
    ]
    plan = _normalize_plan(raw)
    assert plan[0]["assigned_to"] == "selection"  # 非法 agent 回落
    assert "t2" in plan[0]["depends_on"]  # 合法依赖保留
    assert plan[0]["task"] == "调研"


def _base_state(**over):
    state = {
        "goal": "测试目标",
        "user_id": "u1",
        "task_id": "11111111-1111-1111-1111-111111111111",
        "plan": [],
        "results": {},
        "messages": [],
        "pending_approval": None,
        "approved": False,
        "tokens_used": 0,
    }
    state.update(over)
    return state


@pytest.mark.asyncio
async def test_supervisor_goes_to_planner_when_no_plan():
    out = await supervisor_node(_base_state())
    assert out["next"] == "planner"


@pytest.mark.asyncio
async def test_supervisor_respects_dependencies():
    plan = [
        {
            "id": "t1",
            "task": "查到货",
            "assigned_to": "order_logistics",
            "depends_on": [],
            "priority": 1,
        },
        {
            "id": "t2",
            "task": "退款",
            "assigned_to": "order_logistics",
            "depends_on": ["t1"],
            "priority": 2,
        },
    ]
    # t2 依赖 t1，t1 未完成 → 只能先调度 t1
    out = await supervisor_node(_base_state(plan=plan, results={}))
    assert out["next"] == "order_logistics"


@pytest.mark.asyncio
async def test_supervisor_routes_to_critic_when_all_done():
    plan = [
        {"id": "t1", "task": "选品", "assigned_to": "selection", "depends_on": [], "priority": 1}
    ]
    out = await supervisor_node(_base_state(plan=plan, results={"t1": "结果"}))
    assert out["next"] == "critic"


@pytest.mark.asyncio
async def test_supervisor_waits_on_pending_approval():
    out = await supervisor_node(
        _base_state(
            plan=[
                {
                    "id": "t1",
                    "task": "退款",
                    "assigned_to": "order_logistics",
                    "depends_on": [],
                    "priority": 1,
                }
            ],
            pending_approval={"id": "a1", "operation_type": "refund"},
        )
    )
    assert out["next"] == "END"


@pytest.mark.asyncio
async def test_supervisor_resets_stuck_counter_when_progress():
    plan = [
        {"id": "t1", "task": "选品", "assigned_to": "selection", "depends_on": [], "priority": 1}
    ]
    out = await supervisor_node(_base_state(plan=plan, results={}, stuck_rounds=2))
    assert out["next"] == "selection"
    assert out["stuck_rounds"] == 0


@pytest.mark.asyncio
async def test_supervisor_detects_dependency_deadlock():
    # t2 依赖一个永远不会完成的 t1（不在 plan 中）→ 无可调度任务
    plan = [
        {
            "id": "t2",
            "task": "退款",
            "assigned_to": "order_logistics",
            "depends_on": ["t1"],
            "priority": 1,
        }
    ]
    out = await supervisor_node(_base_state(plan=plan, results={}, stuck_rounds=STUCK_LIMIT - 1))
    assert out["next"] == "END"
    assert out["stuck_rounds"] == STUCK_LIMIT
