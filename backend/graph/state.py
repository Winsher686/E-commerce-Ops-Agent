# backend/graph/state.py
# ──────────────────────────────────────────────────────────────
#  LangGraph 共享状态（跨境电商运营中台）
#  在保留原有 goal/plan/results/next 等字段的基础上，扩展：
#    - 运营任务上下文（task_id / task_type / params）
#    - 依赖 DAG 进度与执行轨迹（executed_ops / agent_trace）
#    - 人工审批护栏状态（pending_approval / approvals / hitl_enabled）
#  results 使用 merge 归约器，保证多 Agent 写回时按 subtask id 合并。
# ──────────────────────────────────────────────────────────────
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages


def merge_results(existing: dict, new: dict) -> dict:
    return {**existing, **new}


def merge_list(existing: list, new: list) -> list:
    """追加式归约器：用于 approvals / executed_ops / agent_trace。"""
    return [*(existing or []), *(new or [])]


class AgentState(TypedDict, total=False):
    # ── 用户与目标 ────────────────────────────────────────────
    goal: str
    user_id: str
    messages: Annotated[list, add_messages]

    # ── 运营任务上下文 ────────────────────────────────────────
    task_id: str
    task_type: str          # selection / logistics / pricing / cs / weekly_report / mixed
    params: dict[str, Any]

    # ── 编排 ──────────────────────────────────────────────────
    plan: list              # 依赖感知的任务 DAG（[{id,task,assigned_to,depends_on,priority,requires_approval}]）
    next: str               # 下一个节点
    results: Annotated[dict, merge_results]
    approved: bool
    error: Optional[str]

    # ── 上下文文件 / 知识库 ───────────────────────────────────
    context_files: list

    # ── 人工审批护栏（HITL）───────────────────────────────────
    hitl_enabled: bool
    pending_approval: Optional[dict]
    approvals: Annotated[list, merge_list]
    executed_ops: Annotated[list, merge_list]

    # ── 观测 ──────────────────────────────────────────────────
    agent_trace: Annotated[list, merge_list]
    tokens_used: int
