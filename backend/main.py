# backend/main.py
# ──────────────────────────────────────────────────────────────
#  跨境电商运营中台 —— FastAPI 应用入口
#  - 注册全部路由（含新的 /tasks、/approvals、/agents）
#  - /run 保留为兼容入口：提交运营目标 → 创建运营任务 → 后台编排
#  - /ws/{run_id} 实时推送任务进度与审批请求
# ──────────────────────────────────────────────────────────────
import asyncio
import sys
import uuid

import jwt
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from config import get_settings
from graph.agents.github_agent import GitHubAgent
from graph.rag_flow import run_rag_flow
from graph.router import classify_domain
from routes.admin import router as admin_router
from routes.agents import router as agents_router
from routes.apikeys import router as apikeys_router
from routes.approvals import router as approvals_router
from routes.assistant import router as assistant_router
from routes.auth import limiter
from routes.auth import router as auth_router
from routes.dependencies import get_current_user
from routes.generated_files import router as generated_files_router
from routes.github import router as github_router
from routes.memory import router as memory_router
from routes.plan import router as plan_router
from routes.preferences import router as preferences_router
from routes.system import router as system_router
from routes.tasks import router as tasks_router
from routes.tools import router as tools_router
from routes.updates import router as updates_router
from routes.uploads import router as uploads_router
from services import orchestrator
from services.activity import log_activity
from services.db import get_db, init_db
from tools.github_auth import get_github_token

settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = "HS256"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from bus.redis_bus import publish_event, subscribe_events  # noqa: E402

log = structlog.get_logger()

app = FastAPI(title="Cross-Border Ops API", version="2.0.0")


@app.on_event("startup")
async def startup():
    await init_db()
    log.info("database_pool_initialized")


# ── 注册路由 ──────────────────────────────────────────────────
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(uploads_router)
app.include_router(generated_files_router)
app.include_router(system_router)
app.include_router(plan_router)
app.include_router(apikeys_router)
app.include_router(assistant_router)
app.include_router(github_router)
app.include_router(updates_router)
app.include_router(preferences_router)
app.include_router(memory_router)
app.include_router(tools_router)
# —— 运营中台新增 ——
app.include_router(tasks_router)
app.include_router(approvals_router)
app.include_router(agents_router)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret)

if settings.environment != "production":
    print("REGISTERED ROUTES:")
    for route in app.routes:
        print(getattr(route, "path", route))

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


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.environment, "service": "cross-border-ops"}


# ── 兼容入口：POST /run ───────────────────────────────────────
@app.post("/run")
@limiter.limit("10/minute")
async def run_agent(request: Request, payload: dict, user=Depends(get_current_user)) -> dict:
    run_id = str(uuid.uuid4())
    goal = payload.get("goal", "")
    context_files = payload.get("context_files", [])
    params = payload.get("params", {}) or {}

    pool = await get_db()
    if not pool:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # ── GitHub 命令（沿用原有能力）────────────────────────────
    if goal.startswith("/git"):
        token = await get_github_token(user["id"])
        if not token:
            return {"success": False, "message": "No GitHub token saved"}
        result = GitHubAgent(token).run(goal[1:])
        return {"run_id": run_id, "github": True, "result": str(result)}

    # ── RAG 知识库问答（客服知识库复用）───────────────────────
    if goal.lower().startswith("/rag") and context_files:
        asyncio.create_task(_run_rag(run_id, goal, context_files, str(user["id"])))
        return {"run_id": run_id, "route": "rag"}

    # ── 运营任务：创建后由编排器后台执行 ──────────────────────
    task_type = classify_domain(goal)
    task_id = await orchestrator.create_task_row(str(user["id"]), task_type, goal, params)

    await log_activity("task", "运营任务已创建(/run)", f"[{task_type}] {goal[:80]}")

    asyncio.create_task(
        orchestrator.run_task(
            task_id=task_id, goal=goal, task_type=task_type, params=params,
            context_files=context_files, user_id=str(user["id"]), run_id=run_id,
        )
    )

    return {"run_id": run_id, "task_id": task_id, "route": task_type, "user": user["email"]}


async def _run_rag(run_id: str, goal: str, context_files: list, user_id: str) -> None:
    try:
        result = await run_rag_flow(goal=goal, context_files=context_files, user_id=user_id)
        await publish_event(run_id, {
            "node": "researcher",
            "messages": [result.get("answer", "")],
            "sources": result.get("sources", []),
            "approved": result.get("approved", False),
        })
        await publish_event(run_id, {"type": "done"})
    except Exception as e:  # noqa: BLE001
        await publish_event(run_id, {"node": "system", "messages": [], "error": str(e)})


# ── WebSocket：任务/审批实时事件 ──────────────────────────────
@app.websocket("/ws/{run_id}")
async def websocket_endpoint(ws: WebSocket, run_id: str):
    token = ws.cookies.get("xbops_token")
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
        async for event in subscribe_events(run_id):
            if (event.get("node") == "system" and not event.get("error")
                    and not any(event.get("messages", []))):
                continue
            await ws.send_json(event)
            if event.get("type") in ("done", "task.completed", "task.failed"):
                break
            if event.get("error"):
                break
    except WebSocketDisconnect:
        log.info("ws_disconnected", run_id=run_id)
    except Exception as e:  # noqa: BLE001
        log.error("ws_error", run_id=run_id, error=str(e))
    finally:
        if ws.client_state.name != "DISCONNECTED":
            try:
                await ws.close()
            except RuntimeError:
                pass
