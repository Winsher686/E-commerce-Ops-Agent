# backend/tools/domain/db_query.py
# ──────────────────────────────────────────────────────────────
#  工具：数据库查询（受控只读）
#  用途：周报 Agent 汇总运营数据、客服 Agent 检索知识库。
#  安全：仅允许 SELECT；禁止多语句与写操作。
#  降级：连接池未就绪时返回空结果并标注 source=unavailable。
# ──────────────────────────────────────────────────────────────
import re

from services.db import get_db
from tools.domain._common import load_sample
from tools.domain.schemas import DBQueryInput, DBQueryOutput

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy)\b",
    re.IGNORECASE,
)

# 表名白名单（mock 模式下用于映射到示例数据）
_ALLOWED_TABLES = {
    "order_exceptions",
    "shipments",
    "pricing_history",
    "support_tickets",
    "product_candidates",
    "inventory",
    "competitor_prices",
    "sales_daily",
    "operations_tasks",
    "agent_executions",
    "approvals",
}


async def run(inp: DBQueryInput) -> DBQueryOutput:
    # ── mock 模式：按表名读取示例数据 ─────────────────────────
    from config import get_settings

    if get_settings().mock_enabled:
        if inp.table:
            rows = load_sample(inp.table) or []
            if inp.filters:
                rows = [r for r in rows if all(r.get(k) == v for k, v in inp.filters.items())]
            rows = rows[: inp.limit]
            return DBQueryOutput(task_id=inp.task_id, rows=rows, count=len(rows), source="mock")
        return DBQueryOutput(task_id=inp.task_id, rows=[], count=0, source="mock")

    # ── 真实模式：只读查询 ────────────────────────────────────
    if not inp.sql:
        return DBQueryOutput(task_id=inp.task_id, rows=[], source="error")
    if _FORBIDDEN.search(inp.sql) or ";" in inp.sql.strip().rstrip(";"):
        return DBQueryOutput(task_id=inp.task_id, rows=[], source="blocked")

    pool = await get_db()
    if pool is None:
        return DBQueryOutput(task_id=inp.task_id, rows=[], source="unavailable")

    sql = inp.sql.strip()
    if " limit " not in sql.lower():
        sql = f"{sql.rstrip(';')} LIMIT {inp.limit}"

    async with pool.acquire() as conn:
        records = await conn.fetch(sql)
    rows = [dict(r) for r in records]
    return DBQueryOutput(task_id=inp.task_id, rows=rows, count=len(rows), source="postgres")
