"""
SAQ Worker 设置

启动: uv run python -m saq app.worker.settings

队列 + 定时任务统一由 SAQ 管理，共享 Redis 连接。
新增任务步骤:
1. 在 app/worker/jobs/ 下创建任务函数
2. 在 app/worker/jobs/__init__.py 的 functions 列表中注册
3. 定时任务在 settings.py 的 cron_jobs 列表中添加 CronJob
"""

import dataclasses
import functools
import time

from saq import Queue

from app.core.config import config
from app.core.license import describe, lock_reason
from app.core.logger import logger
from app.services.plugin import PluginRegistry

# ============================================================
# 任务函数注册 — 新增任务后在这里导入并加入列表
# ============================================================

from app.worker.jobs import cron_jobs as _raw_cron_jobs
from app.worker.jobs import functions as _raw_functions

# ============================================================
# 授权门禁 — 未授权/过期时暂停后台任务
#
# 光靠 HTTP 中间件只锁了「界面」，通知重试、查单补单、过期置位、自动结算等
# 定时任务仍会继续跑（等于业务没停）。这里给每个任务函数套一层门禁，
# 同时重建 cron 列表，保证 cron 走同一入口。
# ============================================================

_LOCK_LOG_INTERVAL = 300.0
_lock_logged_at = 0.0


def _log_lock_once(lock: str) -> None:
    global _lock_logged_at
    now = time.monotonic()
    if now - _lock_logged_at < _LOCK_LOG_INTERVAL:
        return
    _lock_logged_at = now
    logger.warning(f"License not active ({lock}): 后台任务已暂停（含定时任务），续期后重启容器恢复")


def license_guard(fn):
    """未授权/过期时直接丢弃任务（不抛异常，避免调用方把错误落到商户通知上）。"""
    @functools.wraps(fn)
    async def guarded(ctx, *args, **kwargs):
        lock = lock_reason()
        if lock:
            _log_lock_once(lock)
            return None
        return await fn(ctx, *args, **kwargs)

    return guarded


_guarded_functions = {fn.__name__: license_guard(fn) for fn in _raw_functions}
functions = [_guarded_functions[fn.__name__] for fn in _raw_functions]
cron_jobs = [
    dataclasses.replace(job, function=_guarded_functions[job.function.__name__])
    for job in _raw_cron_jobs
]

# ============================================================
# Queue 配置
# ============================================================

queue = Queue.from_url(
    (
        f"redis://{config.REDIS_HOST}:{config.REDIS_PORT}"
        if not config.REDIS_PASSWORD
        else f"redis://:{config.REDIS_PASSWORD}@{config.REDIS_HOST}:{config.REDIS_PORT}"
    ),
    name="default",
)


# ============================================================
# Worker Settings
# ============================================================

class WorkerSettings:
    """SAQ Worker 配置"""
    queue = queue
    functions = functions
    cron_jobs = cron_jobs
    concurrency = config.WORKER_CONCURRENCY
    job_timeout = config.WORKER_JOB_TIMEOUT


# ============================================================
# 便捷函数 — 在 FastAPI 路由中投递任务
# ============================================================

async def enqueue(job_name: str, **kwargs) -> str:
    """
    投递任务到队列（SAQ 要求任务参数以关键字形式传入）

    用法:
        from app.worker import enqueue
        job = await enqueue("export_excel", user_id=1)
        # job 是任务 ID 字符串
    """
    lock = lock_reason()
    if lock:
        _log_lock_once(lock)
        return ""
    return await queue.enqueue(job_name, **kwargs)


async def _startup(ctx: dict) -> None:
    """worker 启动钩子：强刷支付插件注册表（通知重试/查单补单依赖插件类）。"""
    PluginRegistry.reload()
    logger.info(f"支付插件注册表已刷新: {', '.join(PluginRegistry.payment_names()) or '无'}")
    # 授权信息展示（强制校验在 config 导入期已完成；失败不退出，进未授权软锁）
    lock = lock_reason()
    if lock:
        logger.warning(f"License not active ({lock}): business APIs are soft-locked, 后台任务已暂停，页面请求返回纯文本 \"Invalid License Code\"")
    else:
        logger.info(f"授权信息: {describe()}")


def settings() -> dict:
    """SAQ CLI 入口（python -m saq app.worker.settings）——返回配置 dict。"""
    return {
        "queue": queue,
        "functions": functions,
        "cron_jobs": cron_jobs,
        "concurrency": config.WORKER_CONCURRENCY,
        "startup": _startup,
    }
