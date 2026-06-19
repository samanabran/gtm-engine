"""Distributed rate limiter for outbound email sends.

Uses Redis sorted sets for a sliding-window rate limit across all
Celery worker processes. Resend allows 2 requests/second -- this
enforces that globally so parallel workers don't 429 each other.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

logger = logging.getLogger("gtm.integrations.rate_limiter")

_REDIS_URL: str | None = None
_redis_pool: Any = None


def _get_redis_url() -> str:
    global _REDIS_URL
    if _REDIS_URL is None:
        url = os.environ.get("CELERY_BROKER_URL") or os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _REDIS_URL = url
    return _REDIS_URL


async def _get_redis() -> Any:
    global _redis_pool
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis.asyncio not available -- rate limiter disabled")
        return None
    if _redis_pool is None:
        url = _get_redis_url()
        _redis_pool = aioredis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    return _redis_pool


async def acquire(
    key: str = "rate_limit:resend_send",
    max_requests: float = 2,
    window: float = 1.0,
    max_wait: float = 30.0,
) -> bool:
    """Wait until a token is available in the sliding window.

    Args:
        key: Redis sorted-set key.
        max_requests: How many requests are allowed per *window*.
        window: Time window in seconds.
        max_wait: Give up after this many seconds and let the call
                  proceed anyway (fail-open rather than deadlock).

    Returns:
        True if the caller should proceed, False if timed out.
    """
    r = await _get_redis()
    if r is None:
        return True

    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        now = time.time()
        cutoff = now - window

        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        results = await pipe.execute()
        count: int = results[1] or 0

        if count < max_requests:
            await r.zadd(key, {now: now})
            await r.expire(key, int(window * 3) + 1)
            return True

        await asyncio.sleep(window / (max_requests * 2))

    logger.warning("rate_limiter.acquire timed out after %.1fs -- letting call through", max_wait)
    return True
