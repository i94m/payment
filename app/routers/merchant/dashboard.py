"""商户端概览：今日/昨日/累计 + 近 7 日趋势 + 成功率 + 公告（merchant_id 隔离）。

统计口径：已支付收款单（status=1 且 order_type=0），充值单不计入收入。
"""
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import col, select

from app.core.deps import SessionDep
from app.core.security import MerchantUser
from app.models import Announcement, Order
from app.responses import suc

router = APIRouter()

_PAID = (Order.status == 1, Order.order_type == 0)


@router.get("")
async def index(session: SessionDep, auth: MerchantUser, days: int = 7):
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    mid = auth.user.id
    paid_cond = (*_PAID, Order.merchant_id == mid)

    async def _range(start, end=None):
        q_count = select(func.count()).select_from(Order).where(*paid_cond, Order.paid_at >= start)
        q_money = select(func.coalesce(func.sum(Order.money), 0)).select_from(Order).where(
            *paid_cond, Order.paid_at >= start)
        if end is not None:
            q_count = q_count.where(Order.paid_at < end)
            q_money = q_money.where(Order.paid_at < end)
        return int((await session.exec(q_count)).one()), Decimal(str((await session.exec(q_money)).one()))

    today_count, today_money = await _range(today_start)
    y_count, y_money = await _range(yesterday_start, today_start)
    total_count, total_money = await _range(datetime(2000, 1, 1))

    # 成功率：近 7 日 已支付 / 全部收款单（含未支付与过期）
    week_ago = today_start - timedelta(days=6)
    all_count = int((await session.exec(
        select(func.count()).select_from(Order).where(
            Order.merchant_id == mid, Order.order_type == 0, Order.created_at >= week_ago)
    )).one())
    paid_week = int((await session.exec(
        select(func.count()).select_from(Order).where(
            *paid_cond, Order.created_at >= week_ago)
    )).one())
    success_rate = round(paid_week / all_count * 100, 2) if all_count else 100.00

    # 近 N 日趋势：单条 GROUP BY 日期（避免逐日两条 SQL 的 N+1）
    start_day = today_start - timedelta(days=days - 1)
    rows = (await session.exec(
        select(
            func.date(Order.paid_at).label("d"),
            func.count().label("c"),
            func.coalesce(func.sum(Order.money), 0).label("m"),
        ).where(*paid_cond, Order.paid_at >= start_day).group_by(func.date(Order.paid_at))
    )).all()
    by_day = {str(r.d): (int(r.c), Decimal(str(r.m))) for r in rows}

    trend = []
    for i in range(days - 1, -1, -1):
        day = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        c, m = by_day.get(day.strftime("%Y-%m-%d"), (0, Decimal("0")))
        trend.append({"date": day.strftime("%m-%d"), "count": c, "money": float(m)})

    # 平台公告（发布中，最新 5 条）
    announces = (await session.exec(
        select(Announcement).where(Announcement.status == 1)
        .order_by(col(Announcement.id).desc()).limit(5)
    )).all()

    return suc({
        "balance": str(auth.user.balance),
        "today": {"count": today_count, "money": float(today_money)},
        "yesterday": {"count": y_count, "money": float(y_money)},
        "total": {"count": total_count, "money": float(total_money)},
        "success_rate": success_rate,
        "rate": str(auth.user.rate) if auth.user.rate is not None else "",
        "trend": trend,
        "announcements": [
            {"id": a.id, "content": a.content,
             "created_at": a.created_at.strftime("%Y-%m-%d") if a.created_at else ""}
            for a in announces
        ],
    })
