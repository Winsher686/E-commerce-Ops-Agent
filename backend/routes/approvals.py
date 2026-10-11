# backend/routes/approvals.py
# ──────────────────────────────────────────────────────────────
#  人工审批 API（HITL）
#    GET  /approvals                     列出审批（可按 status 过滤）
#    GET  /approvals/{approval_id}       审批详情
#    POST /approvals/{approval_id}/approve  通过 → 记录 + 事件 + 恢复执行
#    POST /approvals/{approval_id}/reject   拒绝 → 记录 + 事件
# ──────────────────────────────────────────────────────────────
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from bus import events
from graph.approval import (
    get_approval,
    list_approvals,
    operation_key,
    update_approval,
)
from routes.dependencies import get_current_user
from services import orchestrator

router = APIRouter(prefix="/approvals", tags=["approvals"])


class DecisionRequest(BaseModel):
    reason: str = ""
    decided_by: str | None = None


@router.get("")
async def get_approvals(
    status: str | None = None, limit: int = 100, user=Depends(get_current_user)
) -> dict:
    rows = await list_approvals(status=status, limit=limit)
    return {"approvals": rows, "count": len(rows)}


@router.get("/{approval_id}")
async def get_one(approval_id: str, user=Depends(get_current_user)) -> dict:
    row = await get_approval(approval_id)
    if not row:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if hasattr(row.get("requested_at"), "isoformat"):
        row["requested_at"] = row["requested_at"].isoformat()
    if hasattr(row.get("decided_at"), "isoformat"):
        row["decided_at"] = row["decided_at"].isoformat() if row.get("decided_at") else None
    return row


async def _decide(approval_id: str, approved: bool, body: DecisionRequest, user: dict) -> dict:
    row = await get_approval(approval_id)
    if not row:
        raise HTTPException(status_code=404, detail="审批记录不存在")
    if row.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"审批状态为 {row.get('status')}，不可再操作")

    decided_by = body.decided_by or user.get("email") or user.get("name") or user["user_id"]
    status = "approved" if approved else "rejected"
    await update_approval(approval_id, status, decided_by=decided_by, reason=body.reason or None)

    task_id = str(row["task_id"]) if row.get("task_id") else None
    if task_id:
        if approved:
            await events.emit_approval_granted(
                task_id, approval_id=approval_id, decided_by=decided_by
            )
            # 写入白名单并恢复执行
            key = operation_key(row.get("operation_type", ""), row.get("payload") or {})
            await orchestrator.add_preapproval(task_id, key)
            asyncio.create_task(orchestrator.resume_task(task_id))
        else:
            await events.emit_approval_rejected(
                task_id, approval_id=approval_id, decided_by=decided_by, reason=body.reason
            )
            await orchestrator.update_task_status(task_id, "completed")

    return {
        "approval_id": approval_id,
        "status": status,
        "decided_by": decided_by,
        "resumed": bool(approved and task_id),
    }


@router.post("/{approval_id}/approve")
async def approve(
    approval_id: str, body: DecisionRequest | None = None, user=Depends(get_current_user)
) -> dict:
    return await _decide(approval_id, True, body or DecisionRequest(), user)


@router.post("/{approval_id}/reject")
async def reject(
    approval_id: str, body: DecisionRequest | None = None, user=Depends(get_current_user)
) -> dict:
    return await _decide(approval_id, False, body or DecisionRequest(), user)
