# backend/graph/llm.py
# ──────────────────────────────────────────────────────────────
#  LLM 工厂
#  领域 Agent / Supervisor / planner / critic 统一通过 get_llm() 获取模型。
#  取 Key 优先级：用户自带 Groq Key（user_api_keys）→ 系统 Key（GROQ_API_KEY）。
# ──────────────────────────────────────────────────────────────
from langchain_groq import ChatGroq

from config import get_settings
from services.db import get_db

settings = get_settings()

DEFAULT_MODEL = "llama-3.3-70b-versatile"


async def get_llm(user_id: str) -> ChatGroq:
    """按用户维度返回 ChatGroq 实例（用户 Key 优先，回退系统 Key）。"""
    pool = await get_db()
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                k.groq_key,
                COALESCE(p.model, $2) AS model
            FROM users u
            LEFT JOIN user_api_keys k ON k.user_id = u.id
            LEFT JOIN user_preferences p ON p.user_id = u.id
            WHERE u.id = $1
            """,
            user_id,
            DEFAULT_MODEL,
        )

    user_key = row["groq_key"] if row else None
    model = (row["model"] if row else None) or DEFAULT_MODEL
    api_key = user_key or settings.groq_api_key

    return ChatGroq(model=model, temperature=0, api_key=api_key)
