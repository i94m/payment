import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis
from redis.asyncio.client import Redis

from app.core.config import config

_log = logging.getLogger(__name__)

# 全局连接池
_redis_pool: Redis | None = None

# 缓存客户端类型：Redis 不可用时不降级，直接抛 CacheUnavailableError。
# 直接用 Redis 类型（不用 Union+字符串前向引用）：编译后 FastAPI 以
# 「注解字符串 + 模块全局名」eval 还原依赖，任何未解析 ForwardRef 都会让
# 整个注解还原失败（cache 被当成必填 query 参数）。
CacheClient = Redis


class CacheUnavailableError(RuntimeError):
    """Redis 不可用（连接失败 / ping 失败）。不降级，调用方自行决定重试或报错。"""


async def _get_redis_pool() -> Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.Redis(
            **config.get_redis_config,
            socket_timeout=5,
            socket_connect_timeout=5,
        )
    return _redis_pool


async def get_cache() -> AsyncGenerator[CacheClient, None]:
    """缓存依赖 — Redis 不可用时直接抛 CacheUnavailableError（不静默降级）。

    注意：不在 try/except 块内 yield（asyncgen + yield-in-except 是已知陷阱），
    先完成探测与失败判定，再统一 yield。
    """
    global _redis_pool
    try:
        client = await _get_redis_pool()
        ok = bool(await client.ping())
    except Exception as e:
        _redis_pool = None
        raise CacheUnavailableError(f"Redis 不可用: {e}") from e
    if not ok:
        _redis_pool = None
        raise CacheUnavailableError("Redis ping 失败")
    yield client


@asynccontextmanager
async def get_cache_ctx() -> AsyncGenerator[CacheClient, None]:
    """非 DI 场景（worker 任务/脚本）使用的缓存上下文，失败语义与 get_cache 一致。"""
    global _redis_pool
    try:
        client = await _get_redis_pool()
        ok = bool(await client.ping())
    except Exception as e:
        _redis_pool = None
        raise CacheUnavailableError(f"Redis 不可用: {e}") from e
    if not ok:
        _redis_pool = None
        raise CacheUnavailableError("Redis ping 失败")
    yield client


async def try_lock(cache: CacheClient, key: str, ttl: int = 30) -> bool:
    """分布式锁。

    返回 True=获得锁；False=锁被占用。
    Redis 不可用时抛 CacheUnavailableError，由任务/请求链路自然失败并可见，
    不再静默放行（fail-open）。
    """
    got = await cache.set(key, "1", nx=True, ex=ttl)
    return bool(got)


async def release_lock(cache: CacheClient, key: str) -> None:
    """释放锁（尽力而为：清理动作失败只告警，不把已完成的业务变成失败）。"""
    try:
        await cache.delete(key)
    except Exception:
        _log.warning("释放锁失败 %s", key, exc_info=True)
