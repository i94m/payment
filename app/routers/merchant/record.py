"""商户端资金流水 + 结算记录。"""
from fastapi import APIRouter, Query
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import MerchantUser
from app.models import MerchantRecord, Settlement
from app.responses import paginate, suc

router = APIRouter()

# 流水类型中文名（前端也有映射，后端用于导出与展示兜底）
BIZ_TYPES = {
    "order_income": "订单收入",
    "recharge": "余额充值",
    "settle": "结算扣款",
    "settle_reject": "结算退回",
    "adjust": "调整",
    "deposit_in": "保证金转入",  # 保证金功能已下线，历史流水仍需可读
    "deposit_out": "保证金退回",
    "groupbuy": "购买用户组",  # 用户组功能已下线，历史流水仍需可读
    "refund": "退款",  # 退款功能已下线，历史流水仍需可读
    "invite_rebate": "邀请返现",
}


def _dump_record(r: MerchantRecord) -> dict:
    return {
        "id": r.id,
        "biz_type": r.biz_type,
        "biz_type_name": BIZ_TYPES.get(r.biz_type, r.biz_type),
        "amount": str(r.amount),
        "balance_before": str(r.balance_before),
        "balance_after": str(r.balance_after),
        "order_trade_no": r.order_trade_no or "",
        "remark": r.remark or "",
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


@router.get("/list")
async def record_list(session: SessionDep, auth: MerchantUser,
                      page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                      biz_type: str = "", addtime: str = ""):
    query = select(MerchantRecord).where(
        MerchantRecord.merchant_id == auth.user.id
    ).order_by(col(MerchantRecord.id).desc())
    if biz_type:
        query = query.where(MerchantRecord.biz_type == biz_type)
    if "," in addtime:
        start, end = addtime.split(",", 1)
        if start.strip():
            query = query.where(col(MerchantRecord.created_at) >= start.strip())
        if end.strip():
            query = query.where(col(MerchantRecord.created_at) <= end.strip() + " 23:59:59")
    total = (await session.exec(
        select(func.count()).select_from(query.subquery())
    )).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    return suc(paginate(items=[_dump_record(r) for r in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))


def _dump_settle(s: Settlement) -> dict:
    return {
        "id": s.id,
        "settle_no": s.settle_no,
        "amount": str(s.amount),
        "fee": str(s.fee),
        "real_amount": str(s.real_amount),
        "account": s.account,
        "account_name": s.account_name,
        "settle_type": s.settle_type or 1,
        "status": s.status,
        "remark": s.remark or "",
        "paid_at": s.paid_at.strftime("%Y-%m-%d %H:%M:%S") if s.paid_at else "",
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
    }


@router.get("/settlements")
async def settlement_list(session: SessionDep, auth: MerchantUser,
                          page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                          status: int | str = ""):
    query = select(Settlement).where(
        Settlement.merchant_id == auth.user.id
    ).order_by(col(Settlement.id).desc())
    if status is not None and status != "":
        query = query.where(Settlement.status == int(status))
    total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    return suc(paginate(items=[_dump_settle(s) for s in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))
