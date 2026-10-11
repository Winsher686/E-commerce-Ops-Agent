# backend/tests/test_api_tasks.py
"""运营任务 / Agent 目录 路由逻辑测试（不依赖真实数据库与鉴权）。"""

import pytest

from routes import agents as agents_route
from routes import tasks as tasks_route
from services import orchestrator


@pytest.mark.asyncio
async def test_get_agents_lists_domain_agents():
    out = await agents_route.list_agents(user={"user_id": "u1", "email": "a@b.com"})
    names = {a["name"] for a in out["agents"]}
    assert {
        "supervisor",
        "selection",
        "order_logistics",
        "customer_service",
        "pricing",
        "weekly_report",
        "critic",
    } <= names
    # 工具矩阵包含审批标记
    assert any(t["requires_approval"] for t in out["tools"])


@pytest.mark.asyncio
async def test_create_task_route_auto_classifies(monkeypatch):
    async def fake_create(user_id, task_type, goal, params):
        return "22222222-2222-2222-2222-222222222222"

    async def fake_run(**kwargs):
        return {"status": "completed"}

    async def fake_log(*a, **k):
        return None

    monkeypatch.setattr(orchestrator, "create_task_row", fake_create)
    monkeypatch.setattr(orchestrator, "run_task", fake_run)
    monkeypatch.setattr(tasks_route, "log_activity", fake_log)

    body = tasks_route.CreateTaskRequest(goal="处理本周物流异常，协调补发或退款")
    res = await tasks_route.create_task(
        body, user={"user_id": "u1", "email": "a@b.com", "name": "A", "role": "user"}
    )
    assert res["task_id"] == "22222222-2222-2222-2222-222222222222"
    assert res["task_type"] == "logistics"


@pytest.mark.asyncio
async def test_classify_domain_mixed(monkeypatch):
    from graph.router import classify_domain

    assert classify_domain("同时做选品分析和定价调整") == "mixed"
