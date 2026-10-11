# backend/services/preferences.py
import asyncpg
from config import get_settings
from services.db import get_db

settings = get_settings()

DEFAULT_PREFERENCES = {
    "model": "llama-3.3-70b-versatile",
    "web_search": True,
    "smart_cache": False,
    "smart_suggestion": True,
    "rag_threshold": 1.5,
}


async def get_user_preferences(user_id: str):
    pool = await get_db()

    # 若连接池尚未初始化，退回直连（ssl 由配置控制）。
    if pool is None:
        conn = await asyncpg.connect(settings.database_url, ssl=settings.db_ssl)
        try:
            return await _fetch(conn, user_id)
        finally:
            await conn.close()

    async with pool.acquire() as conn:
        return await _fetch(conn, user_id)


async def _fetch(conn, user_id: str):
    row = await conn.fetchrow(
        """
        SELECT
            model,
            web_search,
            smart_cache,
            smart_suggestion,
            rag_threshold
        FROM user_preferences
        WHERE user_id = $1
        """,
        user_id,
    )

    if not row:
        return DEFAULT_PREFERENCES

    return {
        "model": row["model"],
        "web_search": row["web_search"],
        "smart_cache": row["smart_cache"],
        "smart_suggestion": row["smart_suggestion"],
        "rag_threshold": row["rag_threshold"],
    }
