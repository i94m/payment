"""限流依赖 — Redis 不可用时自动跳过（fastapi-limiter 需要运行期初始化）。"""
from fastapi import Depends, Request, Response
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter


def rate_limit(times: int, minutes: int = 1):
    """返回一个限流依赖。lifespan 未初始化 FastAPILimiter（无 Redis）时直接放行；
    限流器运行故障时也放行（可用性优先）。"""
    limiter = RateLimiter(times=times, minutes=minutes)

    async def dep(request: Request, response: Response):
        if FastAPILimiter.redis is None:
            return
        try:
            await limiter(request, response)
        except Exception:
            return

    return Depends(dep)


def rate_limit_setting(key: str, default: int, minutes: int = 1):
    """运行期可配置限流：次数从设置注册表（settings KV）读取，管理后台即时可调。

    - 值为 0 表示不限流；缺省取 default
    - 无 Redis（限流器未初始化）时先短路放行，不为读设置多付一次查询
    """
    # 延迟导入避免 core → services 的模块环
    from app.core.deps import CacheDep, SessionDep

    async def dep(request: Request, response: Response,
                  session: SessionDep = None, cache: CacheDep = None):
        if FastAPILimiter.redis is None:
            return
        try:
            from app.services.settings import get_setting
            times = int(await get_setting(session, key, default, cache=cache) or 0)
        except Exception:
            times = default
        if times <= 0:
            return
        try:
            await RateLimiter(times=times, minutes=minutes)(request, response)
        except Exception:
            return

    return Depends(dep)
