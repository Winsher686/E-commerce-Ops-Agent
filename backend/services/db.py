# backend/services/db.py
# ──────────────────────────────────────────────────────────────
#  asyncpg 连接池单例 + 集中建表
#  ssl 由 config.db_ssl_mode 控制（本地 docker=disable，云库=require）
# ──────────────────────────────────────────────────────────────
from pathlib import Path
import json

import asyncpg
from config import get_settings

settings = get_settings()

db_pool: asyncpg.Pool | None = None

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def _init_conn(conn: asyncpg.Connection) -> None:
    """把 JSONB / JSON 自动编解码为 Python dict，便于 API 直接返回。"""
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def init_db() -> None:
    """创建连接池并执行集中建表脚本（幂等）。"""
    global db_pool

    db_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=int(getattr(settings, "db_pool_min", 5) or 5),
        max_size=int(getattr(settings, "db_pool_max", 20) or 20),
        ssl=settings.db_ssl,   # ← 本地 docker 用 False，云库用 'require'
        init=_init_conn,
    )

    if SCHEMA_PATH.exists():
        sql = SCHEMA_PATH.read_text(encoding="utf-8")
        async with db_pool.acquire() as conn:
            await conn.execute(sql)


async def get_db() -> asyncpg.Pool | None:
    return db_pool


async def close_db() -> None:
    global db_pool
    if db_pool is not None:
        await db_pool.close()
        db_pool = None
