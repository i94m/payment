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


def settings() -> dict:
    """SAQ CLI 入口（python -m saq app.worker.settings）——返回配置 dict。"""
    return {
        "queue": queue,
        "functions": functions,
        "cron_jobs": cron_jobs,
        "concurrency": config.WORKER_CONCURRENCY,
    }
