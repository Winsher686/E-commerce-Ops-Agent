# backend/main.py
# ──────────────────────────────────────────────────────────────
#  电商运营中台 —— FastAPI 应用入口
#  - 业务路由：/tasks、/approvals、/agents（外加账号/偏好等支撑路由）
#  - /ws/tasks/{task_id} 实时推送任务进度与审批请求
#  - /health 存活探针、/ready 就绪探针
# ──────────────────────────────────────────────────────────────
import asyncio
import sys

import jwt
import structlog
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from bus.events import channel_for_task
from bus.redis_bus import subscribe_events
from config import get_settings
from routes.agents import router as agents_router
from routes.apikeys import router as apikeys_router
from routes.approvals import router as approvals_router
from routes.auth import limiter
from routes.auth import router as auth_router
from routes.preferences import router as preferences_router
from routes.tasks import router as tasks_router
from services.db import get_db, init_db

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = "HS256"
log = structlog.get_logger()

app = FastAPI(title="E-commerce Ops API", version="2.0.0")


@app.on_event("startup")
async def startup():
    await init_db()
    log.info("database_pool_initialized")


# ── 路由注册 ──────────────────────────────────────────────────
app.include_router(auth_router)  # 账号：注册 / 登录 / 会话
app.include_router(apikeys_router)  # 用户自带的 LLM Key
app.include_router(preferences_router)  # 用户偏好（如选用的模型）
# —— 运营中台核心 ——
app.include_router(tasks_router)  # POST /tasks、GET /tasks/{id}、resume
app.include_router(approvals_router)  # GET /approvals、approve / reject
app.include_router(agents_router)  # GET /agents

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


# ── 探针 ──────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """存活探针：进程可用即返回 ok。"""
    return {"status": "ok", "env": settings.environment, "service": "e-commerce-ops"}


@app.get("/ready")
async def ready():
    """就绪探针：确认数据库连接可用。"""
    try:
        pool = await get_db()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ready", "database": "ok"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database unavailable: {e}") from e


# ── WebSocket：任务 / 审批实时事件 ────────────────────────────
@app.websocket("/ws/tasks/{task_id}")
async def ws_task_events(ws: WebSocket, task_id: str):
    """
    订阅 task:{task_id} 频道，实时推送 task.* / agent.* / approval.* 事件。
    鉴权：Cookie 中的 ops_token（JWT）+ 数据库会话校验。
    """
    token = ws.cookies.get("ops_token")
    if not token:
        await ws.close(code=1008)
        return

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        jti = payload.get("jti")
        if not jti:
            await ws.close(code=1008)
            return
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
            await ws.close(code=1008)
            return
    except jwt.InvalidTokenError:
        await ws.close(code=1008)
        return

    await ws.accept()
    try:
        async for event in subscribe_events(channel_for_task(task_id)):
            await ws.send_json(event)
            if event.get("type") in ("task.completed", "task.failed"):
                break
    except WebSocketDisconnect:
        log.info("ws_disconnected", task_id=task_id)
    except Exception as e:  # noqa: BLE001
        log.error("ws_error", task_id=task_id, error=str(e))
    finally:
        if ws.client_state.name != "DISCONNECTED":
            try:
                await ws.close()
            except RuntimeError:
                pass
