"""SAQ 队列监控：worker 存活/队列计数 + 商户通知耗尽聚合。

workers 为空即 worker 全部离线（心跳 TTL 60s），可供外部探活轮询。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.models import Order
from app.responses import suc

router = APIRouter()


@router.post("/stats", dependencies=[Depends(PermissionChecker("queue.view"))])
async def queue_stats(session: SessionDep):
    """队列/通知聚合监控。无 queue.view 由 PermissionChecker 抛 HTTP 403（B11）。"""
    from app.worker import queue
    try:
        info = await queue.info()
        workers = [
            {
                "uuid": uuid,
                "stats": (w.get("stats") or {}),
                "metadata": (w.get("metadata") or {}),
            }
            for uuid, w in (info.get("workers") or {}).items()
        ]
        queue_data = {
            "workers": workers,
            "queued": int(info.get("queued") or 0),
            "active": int(info.get("active") or 0),
            "scheduled": int(info.get("scheduled") or 0),
        }
    except Exception:
        queue_data = {"workers": [], "queued": 0, "active": 0, "scheduled": 0, "error": "queue unreachable"}

    now = datetime.now()
    since = now - timedelta(hours=24)
    exhausted_24h = int((await session.exec(
        select(func.count()).select_from(Order).where(
            col(Order.notify_status) == -1,  # noqa: E712
            col(Order.paid_at).isnot(None),
            col(Order.paid_at) >= since,
        )
    )).one())
    pending_notify = int((await session.exec(
        select(func.count()).select_from(Order).where(
            col(Order.status) == 1,  # noqa: E712
            col(Order.notify_status) > 0,  # noqa: E712
        )
    )).one())

    return suc({
        "queue": queue_data,
        "notify": {
            "exhausted_24h": exhausted_24h,
            "pending": pending_notify,
        },
    })
