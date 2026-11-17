"""
SAQ Worker 设置

启动: uv run python -m saq app.worker.settings

队列 + 定时任务统一由 SAQ 管理，共享 Redis 连接。
新增任务步骤:
1. 在 app/worker/jobs/ 下创建任务函数
2. 在 app/worker/jobs/__init__.py 的 functions 列表中注册
3. 定时任务在 settings.py 的 cron_jobs 列表中添加 CronJob
"""

from saq import Queue

from app.core.config import config
from app.core.license import describe, lock_reason
from app.core.logger import logger
from app.services.plugin import PluginRegistry

# ============================================================
# 任务函数注册 — 新增任务后在这里导入并加入列表
# ============================================================

from app.worker.jobs import functions, cron_jobs  # noqa: F401

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
    return await queue.enqueue(job_name, **kwargs)


async def _startup(ctx: dict) -> None:
    """worker 启动钩子：强刷支付插件注册表（通知重试/查单补单依赖插件类）。"""
    PluginRegistry.reload()
    logger.info(f"支付插件注册表已刷新: {', '.join(PluginRegistry.payment_names()) or '无'}")
    # 授权信息展示（强制校验在 config 导入期已完成；失败不退出，进未授权软锁）
    lock = lock_reason()
    if lock:
        logger.warning(f"License not active ({lock}): business APIs are soft-locked, page requests get plain text \"Invalid License Code\"")
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
