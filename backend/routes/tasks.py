# backend/routes/tasks.py
# ──────────────────────────────────────────────────────────────
#  运营任务 API
#    POST /tasks              提交运营任务
#    GET  /tasks/{task_id}    获取任务状态 / DAG / 结果
#    GET  /tasks/{task_id}/agents  获取各 Agent 执行详情
#    POST /tasks/{task_id}/resume  审批通过后恢复执行
# ──────────────────────────────────────────────────────────────
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from graph.router import classify_domain
from routes.dependencies import get_current_user
from services import orchestrator
from services.activity import log_activity

router = APIRouter(prefix="/tasks", tags=["tasks"])

VALID_TYPES = {"selection", "logistics", "pricing", "cs", "weekly_report", "mixed"}


class CreateTaskRequest(BaseModel):
    goal: str = Field(..., description="运营目标（自然语言）")
    task_type: str | None = Field(default=None, description="可选，留空则自动分类")
    params: dict = Field(
        default_factory=dict, description="业务参数：market/category/sku/order_ids 等"
    )
    context_files: list[str] = Field(default_factory=list)


@router.post("")
async def create_task(
    body: CreateTaskRequest,
    user=Depends(get_current_user),
) -> dict:
    task_type = body.task_type or classify_domain(body.goal)
    if task_type not in VALID_TYPES:
        task_type = "mixed"

    task_id = await orchestrator.create_task_row(user["user_id"], task_type, body.goal, body.params)

    await log_activity("task", "运营任务已创建", f"[{task_type}] {body.goal[:80]}")

    # 后台执行，立即返回
    asyncio.create_task(
        orchestrator.run_task(
            task_id=task_id,
            goal=body.goal,
            task_type=task_type,
            params=body.params,
            context_files=body.context_files,
            user_id=user["user_id"],
        )
    )

    return {"task_id": task_id, "task_type": task_type, "status": "created"}


@router.get("/{task_id}")
async def get_task(task_id: str, user=Depends(get_current_user)) -> dict:
    row = await orchestrator.get_task_row(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    return row


@router.get("/{task_id}/agents")
async def get_task_agents(task_id: str, user=Depends(get_current_user)) -> dict:
    rows = await orchestrator.list_agent_executions(task_id)
    return {"task_id": task_id, "executions": rows, "count": len(rows)}


@router.post("/{task_id}/resume")
async def resume_task(task_id: str, user=Depends(get_current_user)) -> dict:
    row = await orchestrator.get_task_row(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = await orchestrator.resume_task(task_id)
    return {"task_id": task_id, **result}
