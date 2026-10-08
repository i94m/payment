"""代理端首页业绩：今日/昨日/累计 订单数与金额（口径同商户端代理业绩）。"""
from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import AgentUser
from app.models import Order
from app.responses import suc

router = APIRouter()

_PAID_INCOME = (Order.status == 1, Order.order_type == 0, col(Order.is_deducted) == False)  # noqa: E712


@router.get("")
async def dashboard(session: SessionDep, auth: AgentUser):
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    async def _stat(start=None, end=None) -> dict:
        q = select(func.count(), func.coalesce(func.sum(Order.money), 0)).select_from(Order).where(
            *_PAID_INCOME, Order.agent_id == auth.user.id,
        )
        if start is not None:
            q = q.where(Order.paid_at >= start)
        if end is not None:
            q = q.where(Order.paid_at < end)
        cnt, money = (await session.exec(q)).one()
        return {"count": int(cnt), "money": str(money)}

    return suc({
        "today": await _stat(today_start),
        "yesterday": await _stat(yesterday_start, today_start),
        "total": await _stat(),
    })
