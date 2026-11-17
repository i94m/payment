"""回调日志查询。"""
from fastapi import APIRouter, Depends, Query
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.models import NotifyLog
from app.responses import paginated_query_async, suc

router = APIRouter()


def _dump_log(log: NotifyLog) -> dict:
    return {
        "id": log.id,
        "kind": log.kind,
        "trade_no": log.trade_no,
        "url": log.url,
        "request": log.request or "",
        "response": log.response or "",
        "http_status": log.http_status,
        "attempt": log.attempt,
        "success": bool(log.success),
        # 与其它列表页统一 YYYY-MM-DD HH:MM:SS（分页默认序列化会带 T 的 ISO 串）
        "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
    }


@router.post("/list", dependencies=[Depends(PermissionChecker("notify_log.view"))])
async def list_logs(
    session: SessionDep,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    body: dict | None = None,
):
    body = body or {}
    query = select(NotifyLog).order_by(col(NotifyLog.id).desc())
    if body.get("trade_no"):
        query = query.where(NotifyLog.trade_no == body["trade_no"])
    if body.get("kind"):
        query = query.where(NotifyLog.kind == body["kind"])
    if body.get("success") is not None and body.get("success") != "":
        query = query.where(NotifyLog.success == bool(int(body["success"])))

    data = await paginated_query_async(session, query, page, per_page, dump_fn=_dump_log)
    return suc(data)
