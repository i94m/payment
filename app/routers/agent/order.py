"""代理端订单记录：只读列表（归属本代理的订单），日期/状态筛选。

对齐老系统 agent/order.php：七列展示（订单号/金额/实收/商品名称/创建/支付/状态）。

扣量单（is_deducted）与商户端/仪表盘口径一致：状态遮蔽为未支付、无实收、
无支付时间，且按「商户视角」参与状态筛选（筛已支付排除扣量单，筛未支付包含
扣量单）—— 否则代理可凭列表状态与实收反推平台扣量率。
"""
from fastapi import APIRouter, Query

from sqlalchemy import func, or_
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import AgentUser
from app.models import Order
from app.responses import paginate, suc
from app.services.order import payment_type_name_map

router = APIRouter()


def _dump(o: Order, type_names: dict[str, str] | None = None) -> dict:
    deducted = bool(o.is_deducted)
    type_code = o.payment_type_code or ""
    return {
        "trade_no": o.trade_no,
        "type": type_code,
        "type_name": (type_names or {}).get(type_code, type_code),
        "money": str(o.money),
        "realmoney": "" if deducted else (str(o.realmoney) if o.realmoney is not None else ""),
        "subject": o.subject,
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
        "paid_at": "" if deducted else (o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else ""),
        "status": 0 if deducted else o.status,
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
        if status == 1:
            # 代理视角的「已支付」= 平台已支付且未被扣量
            conditions.append(col(Order.status) == 1)
            conditions.append(col(Order.is_deducted) == False)  # noqa: E712
        elif status == 0:
            # 代理视角的「未支付」包含扣量单
            conditions.append(or_(col(Order.status) == 0, col(Order.is_deducted) == True))  # noqa: E712
        else:
            conditions.append(col(Order.status) == status)

    q = select(Order).where(*conditions).order_by(col(Order.id).desc())
    total = (await session.exec(
        select(func.count()).select_from(q.subquery())
    )).one()
    items = (await session.exec(
        q.offset((page - 1) * per_page).limit(per_page)
    )).all()
    type_names = await payment_type_name_map(session)
    return suc(paginate(items=[_dump(o, type_names) for o in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))
