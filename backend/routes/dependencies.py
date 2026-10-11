# backend/routes/dependencies.py
# ──────────────────────────────────────────────────────────────
#  鉴权依赖：从 Cookie(ops_token) 或 Authorization: Bearer 解析 JWT，
#  校验会话有效性并返回当前用户。
# ──────────────────────────────────────────────────────────────
import jwt
from fastapi import Cookie, Header, HTTPException, status

from config import get_settings
from services.db import get_db

settings = get_settings()

SECRET_KEY = settings.jwt_secret
ALGORITHM = "HS256"


async def get_current_user(
    ops_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    token = ops_token
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    jti = payload.get("jti")
    if not jti:
        raise HTTPException(status_code=401, detail="Invalid session")

    pool = await get_db()
    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            """
            SELECT 1 FROM sessions
            WHERE token_jti = $1 AND revoked = FALSE AND expires_at > NOW()
            """,
            jti,
        )
        if not session:
            raise HTTPException(status_code=401, detail="Session expired")

        user = await conn.fetchrow(
            "SELECT id, name, email, role FROM users WHERE id = $1",
            payload["sub"],
        )
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

    return {
        "user_id": str(user["id"]),
        "id": str(user["id"]),
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
    }
