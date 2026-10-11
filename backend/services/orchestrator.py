# backend/services/orchestrator.py
# ──────────────────────────────────────────────────────────────
#  运营任务编排器
#  负责：创建/更新 operations_tasks、驱动 LangGraph、写 agent_executions、
#        发布 Redis 事件、汇总最终结果。
#  该层把“图执行”与“HTTP/WS 层”解耦：路由只需 submit_task / get_task。
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from typing import Any

import structlog

from bus import events
from config import get_settings
from services.db import get_db

log = structlog.get_logger()
settings = get_settings()


# ══════════════════════════════════════════════════════════════
#  数据访问（operations_tasks / agent_executions）
# ══════════════════════════════════════════════════════════════
async def create_task_row(user_id: str, task_type: str, goal: str, params: dict) -> str:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO operations_tasks (user_id, task_type, goal, params, status)
            VALUES ($1,$2,$3,$4::jsonb,'created')
            RETURNING id
            """,
            str(user_id),
            task_type,
            goal,
            json.dumps(params, ensure_ascii=False),
        )
    return str(row["id"])


async def update_task_status(task_id: str, status: str, **cols) -> None:
    pool = await get_db()
    if pool is None:
        return
    sets = ["status = $2", "updated_at = NOW()"]
    args: list[Any] = [task_id, status]
    i = 3
    for k, v in cols.items():
        sets.append(f"{k} = ${i}")
        args.append(v)
        i += 1
    async with pool.acquire() as conn:
        await conn.execute(f"UPDATE operations_tasks SET {', '.join(sets)} WHERE id = $1", *args)


async def get_task_row(task_id: str) -> dict | None:
    pool = await get_db()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM operations_tasks WHERE id = $1", task_id)
    return _row_to_dict(row) if row else None


async def list_agent_executions(task_id: str) -> list[dict]:
    pool = await get_db()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM agent_executions WHERE task_id = $1 ORDER BY started_at ASC", task_id
        )
    return [_row_to_dict(r) for r in rows]


async def _write_execution(
    task_id: str, node: str, output: dict, status: str = "completed"
) -> None:
    pool = await get_db()
    if pool is None:
        return
    subtask_id = None
    if isinstance(output, dict):
        subtask_id = output.get("subtask_id")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO agent_executions
                (task_id, subtask_id, agent, status, output, output_text, finished_at)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,NOW())
            """,
            task_id,
            subtask_id,
            node,
            status,
            json.dumps(output, ensure_ascii=False, default=str),
            _extract_text(output),
        )


def _extract_text(output: Any) -> str:
    if isinstance(output, dict):
        msgs = output.get("messages")
        if msgs:
            last = msgs[-1]
            return getattr(last, "content", str(last))
    return ""


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif isinstance(v, (dict, list)):
            d[k] = v
    return d


# ══════════════════════════════════════════════════════════════
#  图驱动
# ══════════════════════════════════════════════════════════════
DOMAIN_NODES = {
    "selection",
    "order_logistics",
    "customer_service",
    "pricing",
    "weekly_report",
}


def _initial_state(
    task_id: str, goal: str, task_type: str, params: dict, context_files: list, user_id: str
) -> dict:
    return {
        "goal": goal,
        "user_id": user_id,
        "task_id": task_id,
        "task_type": task_type,
        "params": params,
        "context_files": context_files,
        "messages": [],
        "plan": [],
        "results": {},
        "next": "planner",
        "approved": False,
        "error": None,
        "stuck_rounds": 0,
        "hitl_enabled": settings.hitl_enabled,
        "pending_approval": None,
        "approvals": [],
        "executed_ops": [],
        "agent_trace": [],
        "tokens_used": 0,
    }


async def run_task(
    task_id: str,
    goal: str,
    task_type: str,
    params: dict,
    context_files: list,
    user_id: str,
) -> dict:
    """执行运营任务（在后台任务中调用）。完成后返回汇总 dict。"""
    from graph.build_graph import GRAPH

    state = _initial_state(task_id, goal, task_type, params, context_files, user_id)
    config = {"configurable": {"thread_id": task_id}}

    await update_task_status(task_id, "running")
    await events.emit_task_created(task_id, goal=goal, task_type=task_type)

    # ── 累积归约器（不依赖 checkpointer 也能拿到最终结果）──────
    acc_results: dict = {}
    acc_plan: list = []
    acc_ops: list = []
    acc_approvals: list = []
    acc_tokens = 0
    pending_approval = None
    approved = False
    error: str | None = None

    try:
        async for chunk in GRAPH.astream(state, config=config):
            # chunk: {node_name: output}
            for node, output in chunk.items():
                if not isinstance(output, dict):
                    continue

                # 累积
                if output.get("results"):
                    acc_results.update(output["results"])
                if output.get("plan"):
                    acc_plan = output["plan"]
                    await update_task_status(
                        task_id, "running", dag=json.dumps(acc_plan, ensure_ascii=False)
                    )
                    await events.emit_task_decomposed(task_id, dag=acc_plan)
                if output.get("executed_ops"):
                    acc_ops.extend(output["executed_ops"])
                if output.get("approvals"):
                    acc_approvals.extend(output["approvals"])
                if output.get("pending_approval"):
                    pending_approval = output["pending_approval"]
                if output.get("approved"):
                    approved = True
                if output.get("tokens_used"):
                    acc_tokens = max(acc_tokens, int(output["tokens_used"]))
                if output.get("error"):
                    error = output["error"]

                # 记录 Agent 执行 + 事件
                await _write_execution(task_id, node, output)
                if node in DOMAIN_NODES:
                    await events.emit_agent_completed(
                        task_id,
                        agent=node,
                        subtask_id=output.get("subtask_id") if isinstance(output, dict) else None,
                    )

        # ── 收尾 ──────────────────────────────────────────────
        if pending_approval:
            status = "waiting_approval"
        elif error:
            status = "failed"
        else:
            status = "completed"

        result = {
            "approved": approved,
            "tasks": len(acc_plan),
            "results": acc_results,
            "executed_ops": acc_ops,
        }
        await update_task_status(
            task_id,
            status,
            result=json.dumps(result, ensure_ascii=False, default=str),
            tokens_used=acc_tokens,
            error=error,
        )

        if status == "completed":
            await events.emit_task_completed(task_id, result=result)
        elif status == "failed":
            await events.emit_task_failed(task_id, error=error or "unknown")

        # 待审批 → 推送审批请求
        if pending_approval:
            await events.emit_approval_requested(task_id, approval=pending_approval)

        return {"status": status, "approved": approved, "pending_approval": pending_approval}

    except Exception as e:  # noqa: BLE001
        log.error("task_run_failed", task_id=task_id, error=str(e))
        await update_task_status(task_id, "failed", error=str(e))
        await events.emit_task_failed(task_id, error=str(e))
        return {"status": "failed", "error": str(e)}


# ══════════════════════════════════════════════════════════════
#  审批恢复
# ══════════════════════════════════════════════════════════════
async def add_preapproval(task_id: str, key: str) -> None:
    """把已批准的操作键写入任务 params._preapproved，供恢复执行时放行。"""
    row = await get_task_row(task_id)
    if not row:
        return
    params = row.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    pre = list(params.get("_preapproved", []))
    if key not in pre:
        pre.append(key)
    params["_preapproved"] = pre
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE operations_tasks SET params = $2::jsonb, updated_at = NOW() WHERE id = $1",
            task_id,
            json.dumps(params, ensure_ascii=False),
        )


async def resume_task(task_id: str) -> dict:
    """审批通过后恢复执行：带上 _preapproved 白名单重跑 DAG。"""
    row = await get_task_row(task_id)
    if not row:
        return {"status": "not_found"}
    params = row.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params)
    return await run_task(
        task_id=task_id,
        goal=row.get("goal", ""),
        task_type=row.get("task_type", "mixed"),
        params=params,
        context_files=[],
        user_id=str(row.get("user_id") or "anonymous"),
    )
