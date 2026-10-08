"""回调日志查询。"""
from typing import Optional

from fastapi import APIRouter, Query
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import AdminUser, PermissionChecker
from app.models import NotifyLog
from app.responses import paginated_query_async, suc

router = APIRouter()


@router.post("/list")
async def list_logs(
    session: SessionDep,
    auth: AdminUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    body: dict | None = None,
):
    if not auth.is_super_admin and "notify_log.view" not in await auth.get_permission_keys(session):
        from app.responses import err
        return err(403, "forbidden")

    body = body or {}
    query = select(NotifyLog).order_by(col(NotifyLog.id).desc())
    if body.get("trade_no"):
        query = query.where(NotifyLog.trade_no == body["trade_no"])
    if body.get("kind"):
        query = query.where(NotifyLog.kind == body["kind"])
    if body.get("success") is not None and body.get("success") != "":
        query = query.where(NotifyLog.success == bool(int(body["success"])))

    data = await paginated_query_async(session, query, page, per_page)
    return suc(data)
