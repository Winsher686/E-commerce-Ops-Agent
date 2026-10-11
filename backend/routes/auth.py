# backend/routes/auth.py
# ──────────────────────────────────────────────────────────────
#  账号与认证：注册 / 登录 / 退出 / 资料 / 改密 / 注销（+ Google OAuth）
#  建表由 services/schema.sql 统一负责（唯一建表来源）。
# ──────────────────────────────────────────────────────────────
import re
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import get_settings
from routes.dependencies import get_current_user
from services.activity import log_activity
from services.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

limiter = Limiter(key_func=get_remote_address)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id or "",
    client_secret=settings.google_client_secret or "",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

SECRET_KEY = settings.jwt_secret
ALGORITHM = "HS256"
TOKEN_MINUTES = 60 * 24


# ── Schemas ───────────────────────────────────────────────────
class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UpdateProfileRequest(BaseModel):
    name: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


# ── Helpers ───────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(400, "Password too short")
    if not re.search(r"[A-Z]", password):
        raise HTTPException(400, "Must contain uppercase")
    if not re.search(r"[a-z]", password):
        raise HTTPException(400, "Must contain lowercase")
    if not re.search(r"\d", password):
        raise HTTPException(400, "Must contain number")


def create_token(user_id: str, email: str, jti: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "jti": jti,
        "exp": datetime.now(UTC) + timedelta(minutes=TOKEN_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _set_auth_cookie(response: Response, token: str, max_age: int = 3600) -> None:
    response.set_cookie(
        key="ops_token",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="none" if settings.cookie_secure else "lax",
        max_age=max_age,
    )


async def _new_session(conn, user_id, jti: str) -> None:
    await conn.execute(
        """
        INSERT INTO sessions (user_id, token_jti, expires_at)
        VALUES ($1, $2, NOW() + INTERVAL '24 hours')
        """,
        user_id,
        jti,
    )


# ── Routes ────────────────────────────────────────────────────
@router.post("/signup", response_model=AuthResponse)
@limiter.limit("3/minute")
async def signup(request: Request, body: SignupRequest, response: Response):
    pool = await get_db()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM users WHERE email = $1", body.email)
        if existing:
            raise HTTPException(status_code=400, detail="Email already registered")

        validate_password(body.password)
        hashed = hash_password(body.password)

        user = await conn.fetchrow(
            """
            INSERT INTO users (name, email, password)
            VALUES ($1, $2, $3)
            RETURNING id, name, email, role
            """,
            body.name,
            body.email,
            hashed,
        )
        await log_activity("user", "New user registered", user["email"])

        jti = str(uuid.uuid4())
        token = create_token(str(user["id"]), user["email"], jti)
        await _new_session(conn, user["id"], jti)

    _set_auth_cookie(response, token)
    return AuthResponse(
        access_token=token,
        user={
            "id": str(user["id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
        },
    )


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(request: Request, body: LoginRequest, response: Response):
    pool = await get_db()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, name, email, password, role FROM users WHERE email = $1",
            body.email,
        )
        if not user or not verify_password(body.password, user["password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
            )

        jti = str(uuid.uuid4())
        token = create_token(str(user["id"]), user["email"], jti)
        await _new_session(conn, user["id"], jti)

    _set_auth_cookie(response, token)
    return AuthResponse(
        access_token=token,
        user={
            "id": str(user["id"]),
            "name": user["name"],
            "email": user["email"],
            "role": user["role"],
        },
    )


@router.get("/google/login")
async def google_login(request: Request):
    redirect_uri = f"{settings.backend_url}/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(request: Request):
    token = await oauth.google.authorize_access_token(request)
    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("email_verified"):
        return RedirectResponse(url=f"{settings.frontend_url}/login?error=google_auth_failed")

    email = userinfo["email"]
    name = userinfo.get("name", "")

    pool = await get_db()
    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT id, name, email, role FROM users WHERE email = $1",
            email,
        )
        if not user:
            user = await conn.fetchrow(
                """
                INSERT INTO users (name, email, password)
                VALUES ($1, $2, $3)
                RETURNING id, name, email, role
                """,
                name or email.split("@")[0],
                email,
                hash_password(str(uuid.uuid4())),
            )
            await log_activity("user", "New user registered (Google)", email)

        jti = str(uuid.uuid4())
        access_token = create_token(str(user["id"]), user["email"], jti)
        await _new_session(conn, user["id"], jti)

    response = RedirectResponse(url=f"{settings.frontend_url}/dashboard")
    _set_auth_cookie(response, access_token, max_age=60 * 60 * 24)
    return response


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return user


@router.post("/logout")
async def logout(response: Response, ops_token: str | None = Cookie(default=None)):
    if ops_token:
        try:
            payload = jwt.decode(ops_token, SECRET_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            if jti:
                pool = await get_db()
                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE sessions SET revoked = TRUE WHERE token_jti = $1",
                        jti,
                    )
        except jwt.InvalidTokenError:
            pass

    response.delete_cookie(
        key="ops_token",
        httponly=True,
        samesite="none" if settings.cookie_secure else "lax",
    )
    return {"success": True}


@router.patch("/profile")
async def update_profile(body: UpdateProfileRequest, user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET name = $1 WHERE id = $2",
            body.name,
            user["user_id"],
        )
    return {"success": True, "name": body.name}


@router.patch("/password")
async def change_password(body: ChangePasswordRequest, user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        db_user = await conn.fetchrow(
            "SELECT password FROM users WHERE id = $1",
            user["user_id"],
        )
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")
        if not verify_password(body.current_password, db_user["password"]):
            raise HTTPException(status_code=400, detail="Current password incorrect")

        validate_password(body.new_password)
        await conn.execute(
            "UPDATE users SET password = $1 WHERE id = $2",
            hash_password(body.new_password),
            user["user_id"],
        )
    return {"success": True}


@router.delete("/account")
async def delete_account(response: Response, user=Depends(get_current_user)):
    pool = await get_db()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE sessions SET revoked = TRUE WHERE user_id = $1",
            user["user_id"],
        )
        await conn.execute("DELETE FROM users WHERE id = $1", user["user_id"])

    response.delete_cookie(
        key="ops_token",
        httponly=True,
        samesite="none" if settings.cookie_secure else "lax",
    )
    return {"success": True}
