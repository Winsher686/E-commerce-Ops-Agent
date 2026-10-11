# backend/bus/events.py
# ──────────────────────────────────────────────────────────────
#  电商运营中台 —— Redis Pub/Sub 事件规范
#  统一定义事件类型、频道命名与发布辅助函数。
#
#  事件类型：
#    task.created        任务已创建
#    task.decomposed     Supervisor 完成 DAG 分解
#    task.completed      任务全部完成并汇总
#    task.failed         任务失败
#    agent.started       某领域 Agent 开始执行子任务
#    agent.completed     某领域 Agent 完成子任务
#    agent.failed        某领域 Agent 执行失败
#    approval.requested  触发人工审批（HITL 暂停）
#    approval.granted    审批通过
#    approval.rejected   审批拒绝
#    tool.called         工具调用（可观测）
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import time
from typing import Any

from bus.redis_bus import publish_event

# ── 事件类型常量 ──────────────────────────────────────────────
TASK_CREATED = "task.created"
TASK_DECOMPOSED = "task.decomposed"
TASK_COMPLETED = "task.completed"
TASK_FAILED = "task.failed"

AGENT_STARTED = "agent.started"
AGENT_COMPLETED = "agent.completed"
AGENT_FAILED = "agent.failed"

APPROVAL_REQUESTED = "approval.requested"
APPROVAL_GRANTED = "approval.granted"
APPROVAL_REJECTED = "approval.rejected"

TOOL_CALLED = "tool.called"

ALL_EVENT_TYPES = {
    TASK_CREATED,
    TASK_DECOMPOSED,
    TASK_COMPLETED,
    TASK_FAILED,
    AGENT_STARTED,
    AGENT_COMPLETED,
    AGENT_FAILED,
    APPROVAL_REQUESTED,
    APPROVAL_GRANTED,
    APPROVAL_REJECTED,
    TOOL_CALLED,
}


def channel_for_task(task_id: str) -> str:
    """任务事件频道。"""
    return f"task:{task_id}"


def _envelope(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": event_type,
        "ts": time.time(),
        **payload,
    }


async def emit(task_id: str, event_type: str, **payload: Any) -> None:
    """向任务频道发布一条结构化事件。"""
    await publish_event(channel_for_task(task_id), _envelope(event_type, payload))


# ── 便捷封装（供 Agent / 路由层调用）──────────────────────────
async def emit_task_created(task_id: str, **kw) -> None:
    await emit(task_id, TASK_CREATED, **kw)


async def emit_task_decomposed(task_id: str, dag: list, **kw) -> None:
    await emit(task_id, TASK_DECOMPOSED, dag=dag, **kw)


async def emit_task_completed(task_id: str, result: Any = None, **kw) -> None:
    await emit(task_id, TASK_COMPLETED, result=result, **kw)


async def emit_task_failed(task_id: str, error: str, **kw) -> None:
    await emit(task_id, TASK_FAILED, error=error, **kw)


async def emit_agent_started(task_id: str, agent: str, subtask_id: str | None = None, **kw) -> None:
    await emit(task_id, AGENT_STARTED, agent=agent, subtask_id=subtask_id, **kw)


async def emit_agent_completed(
    task_id: str, agent: str, subtask_id: str | None = None, output: Any = None, **kw
) -> None:
    await emit(task_id, AGENT_COMPLETED, agent=agent, subtask_id=subtask_id, output=output, **kw)


async def emit_agent_failed(
    task_id: str, agent: str, error: str, subtask_id: str | None = None, **kw
) -> None:
    await emit(task_id, AGENT_FAILED, agent=agent, subtask_id=subtask_id, error=error, **kw)


async def emit_approval_requested(task_id: str, approval: dict, **kw) -> None:
    await emit(task_id, APPROVAL_REQUESTED, approval=approval, **kw)


async def emit_approval_granted(
    task_id: str, approval_id: str, decided_by: str | None = None, **kw
) -> None:
    await emit(task_id, APPROVAL_GRANTED, approval_id=approval_id, decided_by=decided_by, **kw)


async def emit_approval_rejected(
    task_id: str, approval_id: str, decided_by: str | None = None, reason: str | None = None, **kw
) -> None:
    await emit(
        task_id,
        APPROVAL_REJECTED,
        approval_id=approval_id,
        decided_by=decided_by,
        reason=reason,
        **kw,
    )


async def emit_tool_called(task_id: str, agent: str, tool: str, ok: bool = True, **kw) -> None:
    await emit(task_id, TOOL_CALLED, agent=agent, tool=tool, ok=ok, **kw)
