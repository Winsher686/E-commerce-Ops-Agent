# backend/routes/preferences.py
# ──────────────────────────────────────────────────────────────
#  用户偏好（当前仅模型选择，供 get_llm 使用）
# ──────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from routes.dependencies import get_current_user
from services.db import get_db

router = APIRouter(prefix="/preferences", tags=["preferences"])

DEFAULT_MODEL = "llama-3.3-70b-versatile"


class PreferencesRequest(BaseModel):
    model: str = DEFAULT_MODEL


@router.get("")
async def get_preferences(user=Depends(get_current_user)) -> dict:
    pool = await get_db()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT model FROM user_preferences WHERE user_id = $1",
            user["user_id"],
        )
    model = (row["model"] if row else None) or DEFAULT_MODEL
    return {"success": True, "preferences": {"model": model}}


@router.patch("")
async def save_preferences(payload: PreferencesRequest, user=Depends(get_current_user)) -> dict:
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_preferences (user_id, model)
            VALUES ($1, $2)
            ON CONFLICT (user_id)
            DO UPDATE SET model = EXCLUDED.model, updated_at = NOW()
            """,
            user["user_id"],
            payload.model,
        )
    return {"success": True, "message": "Preferences saved"}
