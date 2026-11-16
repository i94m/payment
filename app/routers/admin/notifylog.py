"""回调日志查询。"""
from fastapi import APIRouter, Depends, Query
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.models import NotifyLog
from app.responses import paginated_query_async, suc
from app.routers.common.params import parse_bool

router = APIRouter()


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
        query = query.where(NotifyLog.success == parse_bool(body["success"], "success"))

    data = await paginated_query_async(session, query, page, per_page)
    return suc(data)
