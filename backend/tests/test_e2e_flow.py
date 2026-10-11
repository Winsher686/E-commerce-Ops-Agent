# backend/tests/test_e2e_flow.py
# ──────────────────────────────────────────────────────────────
#  端到端集成测试（离线）：Mock 数据 + 假 LLM + 屏蔽 Redis，
#  完整跑通「物流异常处理」DAG：planner → order_logistics → critic。
#  验证 BR-B1~B4：识别异常 → 决策处置 → 审批护栏 → 结果闭环。
# ──────────────────────────────────────────────────────────────
import uuid

import pytest

import bus.events as events_mod
import graph.agents._base as base_mod
import graph.agents.critic as critic_mod
import graph.agents.planner as planner_mod
import graph.supervisor as sup_mod


class _FakeResp:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {}


class _FakeLLM:
    """返回非 JSON：让 planner 走确定性回退规划、supervisor 走确定性路由。"""

    async def ainvoke(self, messages, **kwargs):
        return _FakeResp("（离线端到端测试）")


async def _fake_get_llm(user_id: str):
    return _FakeLLM()


async def _noop_publish(*args, **kwargs):
    return None


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # 替换各模块已绑定的 get_llm 引用
    for mod in (base_mod, critic_mod, planner_mod, sup_mod):
        monkeypatch.setattr(mod, "get_llm", _fake_get_llm, raising=False)
    # 屏蔽 Redis 发布（无外部依赖）
    monkeypatch.setattr(events_mod, "publish_event", _noop_publish)


def _initial_state() -> dict:
    return {
        "goal": "处理本周物流异常，协调补发或退款",
        "user_id": "u-e2e",
        "task_id": "11111111-1111-1111-1111-111111111111",
        "task_type": "logistics",
        "params": {"limit": 5},
        "context_files": [],
        "messages": [],
        "plan": [],
        "results": {},
        "next": "planner",
        "approved": False,
        "error": None,
        "stuck_rounds": 0,
        "hitl_enabled": True,
        "pending_approval": None,
        "approvals": [],
        "executed_ops": [],
        "agent_trace": [],
        "tokens_used": 0,
    }


@pytest.mark.asyncio
async def test_logistics_flow_end_to_end_offline():
    from graph.build_graph import GRAPH

    thread = str(uuid.uuid4())
    out = await GRAPH.ainvoke(_initial_state(), config={"configurable": {"thread_id": thread}})

    # 1) 生成了依赖 DAG，且指派给订单物流 Agent
    assert out["plan"], "应生成运营 DAG"
    assert any(t["assigned_to"] == "order_logistics" for t in out["plan"])

    # 2) 子任务有产出
    assert out["results"], "应有子任务结果"

    # 3) 针对异常订单产生了处置动作（补发/退款/催件/转人工）
    assert out["executed_ops"], "应对异常订单产生处置动作"

    # 4) 流程闭环：通过校验，或因高风险操作被审批挂起
    assert out.get("approved") is True or out.get("pending_approval") is not None
