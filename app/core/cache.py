from contextlib import asynccontextmanager
from typing import AsyncGenerator, Union

import redis.asyncio as redis
from redis.asyncio.client import Redis

from app.core.config import config

# 全局连接池
_redis_pool: Redis | None = None

# 缓存客户端类型（Redis 或降级时的 DisabledCache）
CacheClient = Union[Redis, "DisabledCache"]


async def _get_redis_pool() -> Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.Redis(
            **config.get_redis_config,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
    return _redis_pool


class DisabledCache:
    """Redis 不可用时的降级缓存，所有操作静默失败"""

    async def get(self, key: str):
        return None

    async def set(self, key: str, value, **kwargs):
        pass

    async def delete(self, key: str):
        pass

    async def incr(self, key: str):
        return 1

    async def decr(self, key: str):
        return 0

    async def incrbyfloat(self, key: str, amount):
        return 0.0

    async def ttl(self, key: str):
        return -1

    async def expire(self, key: str, seconds: int):
        pass

    async def keys(self, pattern: str):
        return []

    async def ping(self):
        return False

    async def flushdb(self):
        pass

    async def flushall(self):
        pass


async def get_cache() -> AsyncGenerator[CacheClient, None]:
    """缓存依赖 — Redis 不可用时降级为 DisabledCache，不抛异常。

    注意：不在 try/except 块内 yield（asyncgen + yield-in-except 是已知陷阱），
    先完成探测与降级决策，再统一 yield。
    """
    global _redis_pool
    client: CacheClient
    try:
        client = await _get_redis_pool()
        ok = bool(await client.ping())
        if not ok:
            _redis_pool = None
    except Exception:
        _redis_pool = None
        client = DisabledCache()
        ok = False
    if ok:
        yield client
    else:
        yield DisabledCache()


@asynccontextmanager
async def get_cache_ctx() -> AsyncGenerator[CacheClient, None]:
    """非 DI 场景（worker 任务/脚本）使用的缓存上下文，降级行为与 get_cache 一致。"""
    global _redis_pool
    client: CacheClient
    try:
        client = await _get_redis_pool()
        ok = bool(await client.ping())
        if not ok:
            _redis_pool = None
    except Exception:
        _redis_pool = None
        client = DisabledCache()
        ok = False
    if ok:
        yield client
    else:
        yield DisabledCache()


async def try_lock(cache: CacheClient, key: str, ttl: int = 30) -> bool | None:
    """分布式锁。

    返回 True=获得锁；False=锁被占用；None=Redis 不可用（调用方自行决定放行）。
    """
    if isinstance(cache, DisabledCache):
        return None
    try:
        got = await cache.set(key, "1", nx=True, ex=ttl)
        return bool(got)
    except Exception:
        return None


async def release_lock(cache: CacheClient, key: str) -> None:
    try:
        if not isinstance(cache, DisabledCache):
            await cache.delete(key)
    except Exception:
        pass
