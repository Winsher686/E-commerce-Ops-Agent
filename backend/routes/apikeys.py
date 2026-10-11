# backend/routes/apikeys.py
# ──────────────────────────────────────────────────────────────
#  用户自带的 LLM Key 管理
#  领域 Agent 通过 get_llm() 优先使用用户 Key，回退系统 Key。
# ──────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends
from langchain_groq import ChatGroq
from pydantic import BaseModel

from routes.dependencies import get_current_user
from services.db import get_db

router = APIRouter(prefix="/apikeys", tags=["apikeys"])


class APIKeysRequest(BaseModel):
    groq_key: str = ""


@router.get("")
async def get_keys(user=Depends(get_current_user)) -> dict:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT groq_key FROM user_api_keys WHERE user_id = $1",
            user["user_id"],
        )
    return {"groq_key": (row["groq_key"] if row else "") or ""}


@router.patch("")
async def save_keys(payload: APIKeysRequest, user=Depends(get_current_user)) -> dict:
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_api_keys (user_id, groq_key)
            VALUES ($1, $2)
            ON CONFLICT (user_id)
            DO UPDATE SET groq_key = EXCLUDED.groq_key, updated_at = NOW()
            """,
            user["user_id"],
            payload.groq_key,
        )
    return {"success": True}


@router.post("/test/groq")
async def test_groq(user=Depends(get_current_user)) -> dict:
    """用保存的 Key 发一次最小请求，验证可用性。"""
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT groq_key FROM user_api_keys WHERE user_id = $1",
            user["user_id"],
        )
    if not row or not row["groq_key"]:
        return {"success": False, "error": "No Groq key saved"}
    try:
        llm = ChatGroq(model="llama-3.1-8b-instant", api_key=row["groq_key"])
        await llm.ainvoke("hello")
        return {"success": True}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}
