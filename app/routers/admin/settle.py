"""admin 结算管理：列表 / 打款标记 / 拒绝（退回余额）。

打款与驳回都是资金操作：业务侧改结算单与余额，审计侧记「谁批的」
（system_audit_logs，P1-7）。
"""
from typing import Optional

from fastapi import Depends, APIRouter, Query, Request
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import AdminUser, PermissionChecker
from app.models import Merchant, Settlement
from app.responses import err, paginate, suc
from app.services.audit import record_audit
from app.services.settle import mark_settlement_paid, reject_settlement

router = APIRouter()


def _dump_settle(s: Settlement, merchant_username: str = "") -> dict:
    return {
        "id": s.id,
        "settle_no": s.settle_no,
        "merchant_id": s.merchant_id,
        "merchant_username": merchant_username,
        "amount": str(s.amount),
        "fee": str(s.fee),
        "real_amount": str(s.real_amount),
        "account": s.account,
        "account_name": s.account_name,
        "status": s.status,
        "remark": s.remark or "",
        "paid_at": s.paid_at.strftime("%Y-%m-%d %H:%M:%S") if s.paid_at else "",
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
    }


@router.get("/list", dependencies=[Depends(PermissionChecker("settle.view"))])
async def settle_list(session: SessionDep, page: int = Query(1, ge=1),
                      per_page: int = Query(20, ge=1, le=100),
                      status: Optional[int] = Query(None),
                      merchant_id: Optional[int] = Query(None)):
    query = select(Settlement).order_by(col(Settlement.id).desc())
    if status is not None:
        query = query.where(Settlement.status == status)
    if merchant_id is not None:
        query = query.where(Settlement.merchant_id == merchant_id)
    total = (await session.exec(
        select(func.count()).select_from(query.subquery())
    )).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    merchant_ids = {s.merchant_id for s in items}
    name_map = {}
    if merchant_ids:
        merchants = (await session.exec(
            select(Merchant).where(col(Merchant.id).in_(merchant_ids))
        )).all()
        name_map = {m.id: m.username for m in merchants}
    return suc(paginate(
        items=[_dump_settle(s, name_map.get(s.merchant_id, "")) for s in items],
        total=total, page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
    ))


async def _settle_snapshot(session, settle_id: int) -> dict:
    """结算单审计快照（打款/驳回前后）。"""
    s = await session.get(Settlement, settle_id)
    if not s:
        return {}
    return {"settle_no": s.settle_no, "status": s.status,
            "amount": str(s.amount), "real_amount": str(s.real_amount),
            "account": s.account}


@router.post("/{settle_id}/pay", dependencies=[Depends(PermissionChecker("settle.pay"))])
async def pay(settle_id: int, session: SessionDep, request: Request, auth: AdminUser,
              body: dict | None = None):
    before = await _settle_snapshot(session, settle_id)
    ok, msg = await mark_settlement_paid(session, settle_id, (body or {}).get("remark", ""))
    if not ok:
        return err(-1, msg)
    await record_audit(
        session, action="settle.pay", actor=auth, request=request,
        target_type="settlement", target_id=settle_id,
        before=before, after=await _settle_snapshot(session, settle_id),
        remark=(body or {}).get("remark", ""),
    )
    return suc("已标记打款")


@router.post("/{settle_id}/reject", dependencies=[Depends(PermissionChecker("settle.reject"))])
async def reject(settle_id: int, session: SessionDep, request: Request, auth: AdminUser,
                 body: dict | None = None):
    before = await _settle_snapshot(session, settle_id)
    ok, msg = await reject_settlement(session, settle_id, (body or {}).get("remark", ""))
    if not ok:
        return err(-1, msg)
    await record_audit(
        session, action="settle.reject", actor=auth, request=request,
        target_type="settlement", target_id=settle_id,
        before=before, after=await _settle_snapshot(session, settle_id),
        remark=(body or {}).get("remark", ""),
    )
    return suc("已拒绝并退回余额")
