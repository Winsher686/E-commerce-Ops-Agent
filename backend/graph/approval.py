# backend/graph/approval.py
# ──────────────────────────────────────────────────────────────
#  人工审批护栏（Human-in-the-Loop）
#
#  职责：
#   1. 审批矩阵：按操作类型与金额/幅度判定审批层级（auto / supervisor / manager）
#   2. 护栏拦截：领域 Agent 在执行高风险操作前调用 gate()，
#      非 auto 级别时通过 LangGraph interrupt() 暂停图执行，
#      并把审批请求写入 PostgreSQL + 通过 Redis 推送 approval.requested
#   3. 审批落库与决议：approve / reject 更新数据库并记录操作人、时间、理由
#
#  说明：LangGraph 的 interrupt() 需要编译图时挂载 checkpointer。
#  若当前 langgraph 版本不支持 interrupt，则自动降级为
#  “落库 pending + 结束本轮，等待审批接口恢复”，保证系统始终可运行。
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from config import get_settings
from services.db import get_db

settings = get_settings()

# ── 是否支持 LangGraph interrupt ──────────────────────────────
try:  # pragma: no cover - 取决于已安装版本
    from langgraph.types import interrupt as _lg_interrupt  # type: ignore

    HAS_INTERRUPT = True
except Exception:  # noqa: BLE001
    _lg_interrupt = None
    HAS_INTERRUPT = False


# ══════════════════════════════════════════════════════════════
#  1. 审批矩阵
# ══════════════════════════════════════════════════════════════
def level_for_refund(amount_usd: float) -> tuple[str, str]:
    if amount_usd <= settings.approval_refund_auto_usd:
        return "auto", f"退款 {amount_usd} USD ≤ {settings.approval_refund_auto_usd}，自动执行"
    if amount_usd <= settings.approval_refund_supervisor_usd:
        return "supervisor", f"退款 {amount_usd} USD 处于主管审批区间"
    return (
        "manager",
        f"退款 {amount_usd} USD 超过 {settings.approval_refund_supervisor_usd}，需经理审批",
    )


def level_for_reship(amount_usd: float, count: int = 1) -> tuple[str, str]:
    if count >= settings.approval_bulk_reship_count:
        return (
            "supervisor",
            f"批量补发 {count} 件 ≥ {settings.approval_bulk_reship_count}，需主管审批",
        )
    if amount_usd <= settings.approval_reship_auto_usd:
        return "auto", f"补发 {amount_usd} USD ≤ {settings.approval_reship_auto_usd}，自动执行"
    return "supervisor", f"补发 {amount_usd} USD 超过阈值，需主管审批"


def level_for_price_change(pct: float) -> tuple[str, str]:
    p = abs(pct)
    if p <= settings.approval_price_change_auto_pct:
        return "auto", f"改价 {p:.1f}% ≤ {settings.approval_price_change_auto_pct}%，自动执行"
    if p <= settings.approval_price_change_supervisor_pct:
        return "supervisor", f"改价 {p:.1f}% 处于主管审批区间"
    return (
        "manager",
        f"改价 {p:.1f}% 超过 {settings.approval_price_change_supervisor_pct}%，需经理审批",
    )


def decide_level(operation_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    """按操作类型返回 (审批层级, 理由)。"""
    op = (operation_type or "").lower()
    if op == "refund":
        return level_for_refund(float(payload.get("amount_usd", 0)))
    if op in ("reship", "bulk_reship"):
        return level_for_reship(float(payload.get("amount_usd", 0)), int(payload.get("count", 1)))
    if op == "price_change":
        return level_for_price_change(float(payload.get("change_pct", 0)))
    if op in ("inventory_update", "listing_toggle"):
        return "supervisor", f"{op} 属于高风险操作，需主管审批"
    # 未知操作类型：保守起见走主管审批
    return "supervisor", f"未知操作 {op}，默认需主管审批"


def operation_key(operation_type: str, payload: dict[str, Any]) -> str:
    """生成操作的业务主键，用于“已批准操作”在白名单中匹配。"""
    op = (operation_type or "").lower()
    ident = payload.get("order_id") or payload.get("sku") or payload.get("id") or "global"
    return f"{op}:{ident}"


# ══════════════════════════════════════════════════════════════
#  2. 审批请求构建 / 落库
# ══════════════════════════════════════════════════════════════
def build_request(
    agent: str,
    operation_type: str,
    payload: dict[str, Any],
    *,
    task_id: str | None = None,
    subtask_id: str | None = None,
    amount_usd: float = 0.0,
    reason: str = "",
) -> dict[str, Any]:
    level, why = decide_level(operation_type, payload)
    return {
        "id": str(uuid.uuid4()),
        "task_id": task_id,
        "subtask_id": subtask_id,
        "agent": agent,
        "operation_type": operation_type,
        "payload": payload,
        "amount_usd": amount_usd or float(payload.get("amount_usd", 0) or 0),
        "required_level": level,
        "status": "pending",
        "reason": reason or why,
        "decided_by": None,
        "decided_at": None,
        "requested_at": datetime.now(UTC).isoformat(),
    }


async def persist_approval(record: dict[str, Any]) -> str:
    """写入 approvals 表（无连接池时仅返回 id，便于 mock / 测试）。"""
    pool = await get_db()
    if pool is None:
        return record["id"]

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO approvals
                (id, task_id, subtask_id, agent, operation_type, payload,
                 amount_usd, required_level, status, reason, requested_at)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,NOW())
            """,
            record["id"],
            _as_uuid(record.get("task_id")),
            record.get("subtask_id"),
            record["agent"],
            record["operation_type"],
            _json(record.get("payload", {})),
            record.get("amount_usd", 0),
            record["required_level"],
            record.get("status", "pending"),
            record.get("reason", ""),
        )
    return record["id"]


async def update_approval(
    approval_id: str, status: str, decided_by: str | None = None, reason: str | None = None
) -> None:
    pool = await get_db()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE approvals
            SET status = $1,
                decided_by = COALESCE($2, decided_by),
                reason = COALESCE($3, reason),
                decided_at = NOW()
            WHERE id = $4
            """,
            status,
            decided_by,
            reason,
            _as_uuid(approval_id),
        )


async def get_approval(approval_id: str) -> dict | None:
    pool = await get_db()
    if pool is None:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1", _as_uuid(approval_id))
    return dict(row) if row else None


async def list_approvals(status: str | None = None, limit: int = 100) -> list[dict]:
    pool = await get_db()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM approvals WHERE status = $1 ORDER BY requested_at DESC LIMIT $2",
                status,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM approvals ORDER BY requested_at DESC LIMIT $1", limit
            )
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════
#  3. 护栏拦截
# ══════════════════════════════════════════════════════════════
async def gate(
    *,
    agent: str,
    operation_type: str,
    payload: dict[str, Any],
    task_id: str | None = None,
    subtask_id: str | None = None,
    amount_usd: float = 0.0,
    reason: str = "",
    preapproved: list[str] | None = None,
) -> dict[str, Any]:
    """
    高风险操作前的统一入口。返回：
      {"action": "auto"|"approved"|"rejected"|"pending", "approval": <record>}
    - auto      ：阈值内 / HITL 关闭 → 直接执行
    - approved  ：人工已批准（或在 preapproved 白名单中）→ 执行
    - rejected  ：人工拒绝 → 放弃操作
    - pending   ：需要审批但当前无法阻塞（未挂 checkpointer）→ 落库等待
    """
    record = build_request(
        agent,
        operation_type,
        payload,
        task_id=task_id,
        subtask_id=subtask_id,
        amount_usd=amount_usd,
        reason=reason,
    )

    # 自动区间或 HITL 关闭 → 直接放行
    if not settings.hitl_enabled or record["required_level"] == "auto":
        record["status"] = "approved"
        record["decided_by"] = "system(auto)"
        return {"action": "auto", "approval": record}

    # 已批准白名单（审批通过后恢复执行时注入）→ 直接放行
    key = operation_key(operation_type, payload)
    if preapproved and key in preapproved:
        record["status"] = "approved"
        record["decided_by"] = "human(preapproved)"
        return {"action": "approved", "approval": record}

    # 需要人工审批 → 落库
    await persist_approval(record)

    # 若当前图支持 interrupt，则在此暂停，等待人工决议后恢复
    if HAS_INTERRUPT:
        decision = _lg_interrupt(
            {  # type: ignore[misc]
                "approval_id": record["id"],
                "operation_type": operation_type,
                "required_level": record["required_level"],
                "payload": payload,
                "reason": record["reason"],
            }
        )
        # 恢复后 decision 为审批接口写入的 {"approved": bool, "decided_by": str, "reason": str}
        approved = bool((decision or {}).get("approved"))
        record["status"] = "approved" if approved else "rejected"
        record["decided_by"] = (decision or {}).get("decided_by")
        await update_approval(
            record["id"], record["status"], record["decided_by"], (decision or {}).get("reason")
        )
        return {"action": record["status"], "approval": record}

    # 降级：无法阻塞 → 标记待审批，Agent 应结束本轮
    return {"action": "pending", "approval": record}


# ── 工具函数 ──────────────────────────────────────────────────
def _json(v: Any) -> str:
    import json

    return json.dumps(v, ensure_ascii=False, default=str)


def _as_uuid(value: str | None):
    """DB 中 task_id/approval_id 为 UUID；非法值返回 None 以免报错。"""
    if not value:
        return None
    try:
        uuid.UUID(str(value))
        return str(value)
    except (ValueError, TypeError):
        return None
