# backend/graph/agents/_base.py
# ──────────────────────────────────────────────────────────────
#  领域 Agent 公共基座
#  统一处理：子任务挑选、依赖上下文拼接、工具调用、审批护栏、结果写回。
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from graph.approval import gate
from graph.llm import get_llm
from graph.state import AgentState
from tools.domain import registry as tools_registry

log = structlog.get_logger()


async def emit(fn, *args, **kwargs) -> None:
    """安全发布事件：Redis 不可用时不阻塞业务流程。"""
    try:
        await fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        pass


def pick_pending(state: AgentState, agent: str) -> dict | None:
    """挑选该 Agent 名下、依赖已满足、尚未完成的子任务（按优先级）。"""
    results = state.get("results", {})
    pending = [
        t
        for t in sorted(state.get("plan", []), key=lambda x: x.get("priority", 99))
        if t.get("assigned_to") == agent
        and t.get("id") not in results
        and all(d in results for d in t.get("depends_on", []))
    ]
    return pending[0] if pending else None


def dependency_context(state: AgentState, task: dict, limit: int = 2000) -> str:
    results = state.get("results", {})
    parts = []
    for dep in task.get("depends_on", []):
        parts.append(f"前置结果({dep}):\n{str(results.get(dep, ''))[:limit]}")
    return "\n\n".join(parts) or "（无前置依赖）"


async def call_tool(name: str, payload: dict[str, Any]) -> tuple[Any | None, str | None]:
    """调用领域工具并返回 (输出模型, 错误)。"""
    try:
        return await tools_registry.call(name, payload)
    except Exception as e:  # noqa: BLE001
        log.warning("tool_call_failed", tool=name, error=str(e))
        return None, f"{type(e).__name__}: {e}"


async def synthesize(system: str, user: str, user_id: str) -> tuple[str, int]:
    """用 LLM 汇总工具结果，返回 (文本, tokens)。"""
    llm = await get_llm(user_id)
    resp = await llm.ainvoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=user),
        ]
    )
    tokens = getattr(resp, "usage_metadata", {}).get("total_tokens", 0)
    return (resp.content if hasattr(resp, "content") else str(resp)).strip(), tokens


def noop_message(agent: str, label: str) -> dict:
    return {"messages": [AIMessage(content=f"{label}：无待执行任务")]}


def build_result(
    state: AgentState,
    task: dict,
    text: str,
    *,
    tokens_used: int = 0,
    executed_ops: list | None = None,
    approvals: list | None = None,
    pending_approval: dict | None = None,
) -> dict:
    """把领域 Agent 的执行结果写回共享状态。"""
    results = state.get("results", {})
    out: dict[str, Any] = {
        "results": {**results, task.get("id"): text},
        "tokens_used": (state.get("tokens_used") or 0) + tokens_used,
        "messages": [AIMessage(content=text)],
    }
    if executed_ops:
        out["executed_ops"] = executed_ops
    if approvals:
        out["approvals"] = approvals
    if pending_approval:
        out["pending_approval"] = pending_approval
    return out


async def request_approval_if_needed(
    *,
    agent: str,
    operation_type: str,
    payload: dict,
    state: AgentState,
    task: dict,
    amount_usd: float = 0.0,
    reason: str = "",
) -> dict:
    """统一的高风险操作审批入口（内部调用 approval.gate）。"""
    preapproved = (state.get("params", {}) or {}).get("_preapproved", [])
    return await gate(
        agent=agent,
        operation_type=operation_type,
        payload=payload,
        task_id=state.get("task_id"),
        subtask_id=task.get("id"),
        amount_usd=amount_usd,
        reason=reason,
        preapproved=preapproved,
    )


def to_json(model: Any) -> str:
    if model is None:
        return "{}"
    if hasattr(model, "model_dump"):
        return json.dumps(model.model_dump(), ensure_ascii=False, default=str)
    return json.dumps(model, ensure_ascii=False, default=str)
