import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

import redis.asyncio as redis
from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi_limiter import FastAPILimiter
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from app.core.config import config
from app.core.frontend import register_docs, register_frontend
from app.core.license import describe
from app.core.logger import logger, request_id_var
from app.responses import err
from app.routers import routers
from app.services.plugin import PluginRegistry


async def rate_limiter() -> None:
    """初始化限流器，Redis 不可用时跳过（不阻塞启动）"""
    try:
        if config.REDIS_PASSWORD:
            redis_url = f"redis://:{config.REDIS_PASSWORD}@{config.REDIS_HOST}:{config.REDIS_PORT}"
        else:
            redis_url = f"redis://{config.REDIS_HOST}:{config.REDIS_PORT}"
        redis_connection = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        await FastAPILimiter.init(redis_connection)
        logger.info("Rate limiter initialized")
    except Exception:
        # init 半途失败也会留下 redis 引用，必须清空，否则限流依赖会误判可用
        FastAPILimiter.redis = None
        logger.warning("Redis unavailable, rate limiter disabled")


async def bootstrap_platform_rsa_key() -> None:
    """平台 RSA 私钥自检：为空则自动生成并落库。

    保证平台公钥始终可由私钥派生，API 响应/异步通知签名不缺位；
    失败仅告警，不阻塞启动。缓存失效避免 Redis 旧快照（无该 key）延迟生效。
    """
    from app.core.cache import get_cache_ctx
    from app.core.database import session_maker
    from app.services.settings import ensure_platform_rsa_key, invalidate_cache

    try:
        async with session_maker() as session:
            generated = await ensure_platform_rsa_key(session)
        if generated:
            async with get_cache_ctx() as cache:
                await invalidate_cache(cache)
            logger.info("平台RSA私钥未配置，已自动生成（公钥由私钥实时派生）")
    except Exception:
        logger.warning("平台RSA私钥自检失败，跳过（不影响启动）")


@asynccontextmanager
async def lifespan(instance: FastAPI):
    # 应用启动前执行
    if config.APP_ENV == "local":
        logger.warning("APP_ENV=local：登录验证码接受万能码 0000，仅供本地开发/测试，生产环境必须设为 prod")
    # 授权信息展示（仅可读描述；强制校验在 config 导入期已完成）
    logger.info(f"授权信息: {describe()}")
    # 启动即强刷插件注册表（与后台「已安装插件」刷新同源），不等首次使用或手动点击
    PluginRegistry.reload()
    logger.info(f"支付插件注册表已刷新: {', '.join(PluginRegistry.payment_names()) or '无'}")
    await rate_limiter()
    await bootstrap_platform_rsa_key()
    yield
    # 应用关闭时运行
    from app.services.http_client import close_http_client as close_shared_client
    await close_shared_client()


app = FastAPI(
    lifespan=lifespan,
    title=config.APP_NAME,
    # 关闭内置 Swagger/Re-doc/OpenAPI：/docs 留给 VitePress 对接文档站
    # （app/core/frontend.py::register_docs），同时不对外暴露内部接口 Schema
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# 跨域
_origins = [o.strip() for o in config.APP_CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=len(_origins) == 1 and _origins[0] == "*",
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(routers)


# ============================================================
# request-id 中间件：透传/生成 X-Request-Id，绑定到日志 contextvar，
# 并记录每条请求的 method/path/status/duration，便于链路追踪与慢请求定位。
# ============================================================
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-Id"] = rid
        return response
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "req %s %s status=%s duration_ms=%.1f",
            request.method, request.url.path, status_code, duration_ms,
        )
        request_id_var.reset(token)

# 健康检查
@app.get("/health")
async def health():
    return JSONResponse({"message": "ok"})

# 对接文档（VitePress 静态站点，公开访问）— 必须先于 SPA 兜底路由注册
register_docs(app)

# 前端静态资源 + SPA fallback（static/console 不存在时自动跳过）
register_frontend(app)

# 挂载 storage
class GuardedStaticFiles(StaticFiles):
    """/storage 敏感目录拒绝列表。

    storage 根目录整体挂载（上传 URL 形如 /storage/{key}，key 首级目录任意，
    如 image/…、public/…），但同根下还有 logs/private/certs/exports 等运维
    目录——日志含收银台令牌路径、jti 等敏感信息，必须对 HTTP 不可达。
    """

    DENIED_TOP_DIRS = {"logs", "private", "certs", "exports", "tmp"}

    async def get_response(self, path: str, scope):
        parts = PurePosixPath(path).parts
        if parts and parts[0] in self.DENIED_TOP_DIRS:
            raise StarletteHTTPException(status_code=404)
        return await super().get_response(path, scope)


_base_dir = os.path.dirname(__file__)
storage_dir = os.path.join(_base_dir, "storage")
if os.path.exists(storage_dir):
    app.mount("/storage", GuardedStaticFiles(directory=storage_dir), name="storage")


# ============================================================
# 异常处理 — 统一 JSON：{code, message, errors?}
# ============================================================

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    msg = detail.get("message") or detail.get("msg") if isinstance(detail, dict) else str(detail)
    if isinstance(detail, dict):
        code = detail.get("code", exc.status_code)
        errors = detail.get("errors")
    else:
        code, errors = exc.status_code, None
    return err(code=code, message=str(msg or "error"), errors=errors, http_status=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors_list = []
    for e in exc.errors():
        errors_list.append({
            "field": ".".join(str(loc) for loc in e["loc"]),
            "message": e["msg"],
            "type": e["type"],
        })
    logger.warning(f"Validation error: {errors_list}")
    return err(code=422, message="请求参数不正确", errors=errors_list, http_status=422)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return err(code=400, message=str(exc), http_status=400)


@app.exception_handler(AttributeError)
async def attribute_error_handler(request: Request, exc: AttributeError):
    logger.warning(f"AttributeError: {exc}", exc_info=True)
    return err(code=500, message="系统内部错误，请稍后再试", http_status=500)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", exc_info=True)
    return err(code=500, message="系统内部错误，请稍后再试", http_status=500)
