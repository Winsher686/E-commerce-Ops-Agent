# backend/bus/redis_bus.py
# ──────────────────────────────────────────────────────────────
#  Redis Pub/Sub 事件总线（电商运营中台）
#
#  频道命名：task:{task_id} —— 运营任务事件流（task.* / agent.* / approval.*）
#  ──────────────────────────────────────────────────────────────
import json
from collections.abc import AsyncGenerator

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


async def publish_event(channel: str, event: dict) -> None:
    """向指定频道发布一条 JSON 事件。"""
    async with _client() as r:
        await r.publish(channel, json.dumps(event, default=str))


async def subscribe_events(channel: str) -> AsyncGenerator[dict, None]:
    """订阅频道并逐条 yield 解析后的事件。"""
    async with _client() as r:
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        async for message in pubsub.listen():
            if message["type"] == "message":
                yield json.loads(message["data"])
