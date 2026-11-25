"""admin 数据统计：总业绩（按天看收入）。

口径与首页 `_today_stats` 对齐，避免同一笔钱在两个页面算出不同数：

- 收款：`status=1`（已支付）且非测试单，按 `paid_at` 归属日期；
  收款金额取 `realmoney`（买家实付），与首页「支付方式收入统计」同口径
- 扣量：`is_deducted=1`，按 `created_at` 归属日期（首页「今日/昨日扣量」就是按创建时间），
  金额取申报金额 `money`，与首页卡片一致
- 平台利润：普通单 `profit`；扣量单再加回未被支付给商户的 `merchant_income`
  （扣量单商户未入账，平台实际留存是 realmoney - channel_cost），按 `paid_at` 归属

区间内无单的日期补 0 行，前端曲线不断档。默认近 30 天，最多 90 天（避免一次拉全量历史）。
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import Depends, APIRouter, Query
from sqlalchemy import case
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.models import Order
from app.responses import err, suc
from app.services.order import ORDER_TYPE_TEST

router = APIRouter()

# 单次查询的最大天数（含首尾）
MAX_DAYS = 90
DEFAULT_DAYS = 30


def _f(v) -> float:
    return round(float(Decimal(str(v or 0))), 2)


def _parse_day(raw: str, name: str) -> tuple[date | None, str]:
    """解析 YYYY-MM-DD；空串返回 (None, "")，格式错误返回错误文案。"""
    raw = (raw or "").strip()
    if not raw:
        return None, ""
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), ""
    except ValueError:
        return None, f"{name}格式错误，应为 YYYY-MM-DD"


@router.get("/daily", dependencies=[Depends(PermissionChecker("dashboard.view"))])
async def daily_performance(session: SessionDep,
                            start_date: str = Query(""), end_date: str = Query("")):
    """按天汇总收款 / 扣量 / 平台利润（「总业绩统计」页）。"""
    today = datetime.now().date()
    start, msg = _parse_day(start_date, "开始日期")
    if msg:
        return err(-1, msg)
    end, msg = _parse_day(end_date, "结束日期")
    if msg:
        return err(-1, msg)
    # 只给一端时以另一端为锚补齐区间，不静默忽略
    if start is None and end is None:
        end = today
        start = end - timedelta(days=DEFAULT_DAYS - 1)
    elif start is None:
        start = end - timedelta(days=DEFAULT_DAYS - 1)
    elif end is None:
        end = start + timedelta(days=DEFAULT_DAYS - 1)
    if end < start:
        return err(-1, "结束日期不能早于开始日期")
    if (end - start).days + 1 > MAX_DAYS:
        return err(-1, f"查询区间最多 {MAX_DAYS} 天")

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

    # ---- 收款 + 平台利润（已支付、非测试，按支付时间归日）----
    profit_expr = func.coalesce(col(Order.profit), 0) + case(
        (col(Order.is_deducted) == True,  # noqa: E712
         func.coalesce(col(Order.merchant_income), 0)),
        else_=0,
    )
    paid_day = func.date(Order.paid_at)
    paid_rows = (await session.exec(
        select(
            paid_day.label("d"),
            func.count().label("c"),
            func.coalesce(func.sum(Order.realmoney), 0).label("m"),
            func.coalesce(func.sum(profit_expr), 0).label("p"),
        )
        .select_from(Order)
        .where(col(Order.status) == 1,
               col(Order.order_type) != ORDER_TYPE_TEST,
               col(Order.paid_at) >= start_dt,
               col(Order.paid_at) < end_dt)
        .group_by(paid_day)
    )).all()

    # ---- 扣量（命中即 status=1 + is_deducted=1；按创建时间归日，与首页卡片同口径）----
    deduct_day = func.date(Order.created_at)
    deduct_rows = (await session.exec(
        select(
            deduct_day.label("d"),
            func.count().label("c"),
            func.coalesce(func.sum(Order.money), 0).label("m"),
        )
        .select_from(Order)
        .where(col(Order.is_deducted).is_(True),
               col(Order.created_at) >= start_dt,
               col(Order.created_at) < end_dt)
        .group_by(deduct_day)
    )).all()

    def _key(d) -> str:
        return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]

    paid_map = {_key(d): (int(c or 0), _f(m), _f(p)) for d, c, m, p in paid_rows}
    deduct_map = {_key(d): (int(c or 0), _f(m)) for d, c, m in deduct_rows}

    items = []
    total_paid_count = total_paid_money = total_deduct_count = 0
    total_deduct_money = total_profit = 0.0
    days = (end - start).days + 1
    for i in range(days):
        day = start + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        paid_count, paid_money, profit = paid_map.get(key, (0, 0.0, 0.0))
        deduct_count, deduct_money = deduct_map.get(key, (0, 0.0))
        items.append({
            "date": key,
            "paid_count": paid_count,
            "paid_money": paid_money,
            "deduct_count": deduct_count,
            "deduct_money": deduct_money,
            "profit": profit,
        })
        total_paid_count += paid_count
        total_paid_money = round(total_paid_money + paid_money, 2)
        total_deduct_count += deduct_count
        total_deduct_money = round(total_deduct_money + deduct_money, 2)
        total_profit = round(total_profit + profit, 2)

    return suc({
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "items": items,
        "total": {
            "paid_count": total_paid_count,
            "paid_money": total_paid_money,
            "deduct_count": total_deduct_count,
            "deduct_money": total_deduct_money,
            "profit": total_profit,
        },
    })
