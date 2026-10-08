"""代理端订单记录：只读列表（归属本代理的订单），日期/状态筛选。

对齐老系统 agent/order.php：七列展示（订单号/金额/实收/商品名称/创建/支付/状态），
状态原样返回（不做商户侧扣量遮蔽）。
"""
from fastapi import APIRouter, Query

from sqlalchemy import func
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import AgentUser
from app.models import Order
from app.responses import paginate, suc

router = APIRouter()


def _dump(o: Order) -> dict:
    return {
        "trade_no": o.trade_no,
        "money": str(o.money),
        "realmoney": str(o.realmoney) if o.realmoney is not None else "",
        "subject": o.subject,
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
        "paid_at": o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else "",
        "status": o.status,
    }


@router.get("/list")
async def order_list(session: SessionDep, auth: AgentUser,
                     page: int = Query(1, ge=1), per_page: int = Query(15, ge=1, le=100),
                     starttime: str = Query("", max_length=10),
                     endtime: str = Query("", max_length=10),
                     status: int | None = Query(None)):
    conditions = [Order.agent_id == auth.user.id]
    if starttime:
        conditions.append(col(Order.created_at) >= f"{starttime} 00:00:00")
    if endtime:
        conditions.append(col(Order.created_at) <= f"{endtime} 23:59:59")
    if status is not None:
        conditions.append(col(Order.status) == status)

    q = select(Order).where(*conditions).order_by(col(Order.id).desc())
    total = (await session.exec(
        select(func.count()).select_from(q.subquery())
    )).one()
    items = (await session.exec(
        q.offset((page - 1) * per_page).limit(per_page)
    )).all()
    return suc(paginate(items=[_dump(o) for o in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))
