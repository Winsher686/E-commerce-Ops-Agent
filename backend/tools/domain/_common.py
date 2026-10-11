# backend/tools/domain/_common.py
# ──────────────────────────────────────────────────────────────
#  领域工具公共支撑：
#    - 示例数据加载（mock 模式）
#    - 真实 API 调用辅助（httpx + 超时 + 重试）
#    - 统一的“工具执行包装器”：超时 / 重试 / 缺失降级
# ──────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from typing import Any, Awaitable, Callable, Optional

import httpx

from config import get_settings

settings = get_settings()

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "sample_data")


@lru_cache(maxsize=32)
def load_sample(name: str) -> Any:
    """加载 sample_data/<name>.json（不存在则返回空结构）。"""
    path = os.path.abspath(os.path.join(SAMPLE_DIR, f"{name}.json"))
    if not os.path.exists(path):
        return [] if name.endswith("s") else {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def http_get(
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> dict:
    """真实 API GET（mock 模式不会调用）。"""
    async with httpx.AsyncClient(timeout=timeout or settings.tool_timeout_seconds) as client:
        res = await client.get(url, params=params, headers=headers or {})
        res.raise_for_status()
        return res.json()


async def http_post(
    url: str,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: Optional[int] = None,
) -> dict:
    """真实 API POST（mock 模式不会调用）。"""
    async with httpx.AsyncClient(timeout=timeout or settings.tool_timeout_seconds) as client:
        res = await client.post(url, json=json_body or {}, headers=headers or {})
        res.raise_for_status()
        return res.json()


async def run_tool(
    name: str,
    fn: Callable[[], Awaitable[Any]],
    fallback: Any = None,
) -> tuple[Any, Optional[str]]:
    """
    工具执行包装器：带超时与重试。
    返回 (结果, 错误信息)；全部失败时返回 (fallback, error)。

    Agent 的失败降级策略统一依赖此封装：调用失败时获得 fallback，
    并在 error 中拿到原因，用于写入执行记录与消息提示。
    """
    last_err: Optional[str] = None
    attempts = 1 + max(0, settings.tool_max_retries)

    for i in range(attempts):
        try:
            result = await asyncio.wait_for(fn(), timeout=settings.tool_timeout_seconds)
            return result, None
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            if i < attempts - 1:
                await asyncio.sleep(0.3 * (i + 1))

    return fallback, last_err
