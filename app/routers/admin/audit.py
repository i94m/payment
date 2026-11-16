"""后台审计日志查询（只读）。

审计行由 `app/services/audit.record_audit` 在敏感操作成功后写入，
本路由只提供查询：按动作 / 操作人 / 对象 / 时间段过滤，倒序分页。

权限键 `system.audit`（系统管理组）。审计含资金操作前后值，默认仅授予
超管/运维角色；给普通运营开 `system.audit` 前请确认其可见范围可接受。
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.models import SystemAuditLog
from app.responses import paginate, suc

router = APIRouter()


def _parse_dt(raw: str | None, *, end_of_day: bool = False) -> Optional[datetime]:
    """容错解析时间过滤参数（`?start=abc` 直接忽略，绝不 500）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    return None


def _opt_int(raw: str | None) -> Optional[int]:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _dump(row: SystemAuditLog) -> dict:
    return {
        "id": row.id,
        "actor_guard": row.actor_guard,
        "actor_id": row.actor_id,
        "actor_name": row.actor_name or "",
        "action": row.action,
        "target_type": row.target_type or "",
        "target_id": row.target_id or "",
        "before": row.before,
        "after": row.after,
        "ip": row.ip or "",
        "user_agent": row.user_agent or "",
        "remark": row.remark or "",
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
    }


@router.get("/list", dependencies=[Depends(PermissionChecker("system.audit"))])
async def audit_list(session: SessionDep,
                     page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                     action: str = "", target_type: str = "", target_id: str = "",
                     actor_id: str = "", guard: str = "",
                     start: str = "", end: str = ""):
    query = select(SystemAuditLog)
    if action.strip():
        query = query.where(col(SystemAuditLog.action).contains(action.strip()))
    if target_type.strip():
        query = query.where(SystemAuditLog.target_type == target_type.strip())
    if target_id.strip():
        query = query.where(SystemAuditLog.target_id == target_id.strip())
    if guard.strip():
        query = query.where(SystemAuditLog.actor_guard == guard.strip())
    if (aid := _opt_int(actor_id)) is not None:
        query = query.where(SystemAuditLog.actor_id == aid)
    if (dt := _parse_dt(start)):
        query = query.where(col(SystemAuditLog.created_at) >= dt)
    if (dt := _parse_dt(end, end_of_day=True)):
        query = query.where(col(SystemAuditLog.created_at) <= dt)

    total = (await session.exec(
        select(func.count()).select_from(query.subquery())
    )).one()
    rows = (await session.exec(
        query.order_by(col(SystemAuditLog.id).desc())
        .offset((page - 1) * per_page).limit(per_page)
    )).all()
    return suc(paginate(
        items=[_dump(r) for r in rows], total=total, page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
    ))


@router.get("/actions", dependencies=[Depends(PermissionChecker("system.audit"))])
async def audit_actions(session: SessionDep):
    """已出现过的动作名（前端筛选下拉）。"""
    rows = (await session.exec(
        select(SystemAuditLog.action).distinct().order_by(col(SystemAuditLog.action))
    )).all()
    return suc([str(r) for r in rows if r])
