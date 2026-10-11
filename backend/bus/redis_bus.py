# backend/bus/redis_bus.py
# ──────────────────────────────────────────────────────────────
#  Redis Pub/Sub 事件总线（跨境电商运营中台）
#
#  频道命名：
#    run:{run_id}    —— 兼容旧的通用运行事件流
#    task:{task_id}  —— 运营任务事件流（task.* / agent.* / approval.*）
#
#  为了向后兼容，publish_event / subscribe_events 接受“完整频道名”，
#  若传入的是不含冒号的裸 run_id，则自动补全为 run:{run_id}。
# ──────────────────────────────────────────────────────────────
import json
from typing import AsyncGenerator

import redis.asyncio as aioredis

from config import get_settings

settings = get_settings()


def _use_tls() -> bool:
    """TLS 开关：显式配置优先，否则按 URL scheme 自动判断。"""
    if settings.redis_use_tls is not None:
        return settings.redis_use_tls
    return settings.redis_url.startswith("rediss://")


def _client() -> aioredis.Redis:
    url = settings.redis_url
    if _use_tls() and url.startswith("redis://"):
        url = url.replace("redis://", "rediss://", 1)
    return aioredis.from_url(url, decode_responses=True)


def channel_for_run(run_id: str) -> str:
    return f"run:{run_id}"


def channel_for_task(task_id: str) -> str:
    return f"task:{task_id}"


def _normalize(channel: str) -> str:
    """裸 run_id（无冒号）→ run:{id}；已是完整频道则原样返回。"""
    return channel if ":" in channel else f"run:{channel}"


async def publish_event(channel: str, event: dict) -> None:
    print("PUBLISH:", channel, event)
    async with _client() as r:
        await r.publish(_normalize(channel), json.dumps(event, default=str))


async def subscribe_events(channel: str) -> AsyncGenerator[dict, None]:
    async with _client() as r:
        pubsub = r.pubsub()
        await pubsub.subscribe(_normalize(channel))
        print("SUBSCRIBED:", channel)
        async for message in pubsub.listen():
            if message["type"] == "message":
                yield json.loads(message["data"])
