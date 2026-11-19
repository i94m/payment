"""数据看板：首页统计点 + 首页快捷配置。

统计点：
- 订单总数 / 商户数量
- 总计余额（商户余额合计）/ 结算总额（结算单合计）
- 今日/昨日扣量额度（扣量订单金额合计，is_deducted）—— 运营口径的「预计收入」
- 今日订单成功率；近期（60/30/10/1 分钟）订单成功率与已支付成功笔数
- 支付方式收入统计 / 支付通道收入统计 / 支付方式手续费利润（仅今日）

所有统计实时计算（不缓存，保证运营看到的是当前值），但查询已收敛：
近期成功率 1 条、扣量 1 条、计数 1 条、余额/结算 1 条、趋势 1 条
（原实现按天 ×2 条循环）、今日/累计各 1 条 —— 首页固定 10 条查询内，
配合 orders 的 (status, paid_at) 复合索引（迁移 0004）走索引范围扫描。

快捷配置：
- 金额路由：按支付方式配置金额区间 → 固定通道（写入 payroute_config，choose_channel 生效）
- 通道限速：按通道配置 次/分钟（写入 channel_ratelimit，choose_channel 生效），附近期成功率
"""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import Depends, APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, case
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.deps import SessionDep
from app.core.security import AdminUser, PermissionChecker
from app.models import Channel, Merchant, Order, PaymentType, Settlement
from app.responses import err, suc
from app.services.home_config import (
    get_payroute_config,
    get_ratelimit_last,
    get_ratelimit_map,
    replace_ratelimit_map,
    set_payroute_item,
    set_payroute_items,
    set_ratelimit_last,
    set_ratelimit_one,
)
from app.services.settings import get_all_settings, setting_enabled
from app.services.order import ORDER_TYPE_TEST
from app.services.test_pay import TEST_MERCHANT_PID

router = APIRouter()


def _rate(success: int, total: int) -> float:
    """成功率：分母为 0（窗口内无订单）按 0% 计。"""
    return round(success / total * 100, 2) if total > 0 else 0.0


def _f(v) -> float:
    return float(Decimal(str(v or 0)))


# ============================================================
# 首页统计
# ============================================================

async def _today_stats(session: AsyncSession, today_start: datetime) -> dict:
    """今日收入/利润统计。

    结构：
      {all, profit_all, paytype: {支付方式ID: 实收}, channel: {通道ID: 实收}, profit_paytype: {支付方式ID: 利润}}
    口径：status=1（已支付），实收=realmoney（买家实付）＝成交流水，按支付时间归属当日。
    利润=**平台留存**：普通单 profit（realmoney - merchant_income - channel_cost），
    扣量单额外加回 merchant_income —— 扣量单商户未入账，平台实际留存是 realmoney - channel_cost，
    不加回则利润被系统性低估，财务对不上账。
    """
    # 平台留存：扣量单的 merchant_income 未被支付给商户，计入平台利润
    profit_expr = func.coalesce(col(Order.profit), 0) + case(
        (col(Order.is_deducted) == True,  # noqa: E712
         func.coalesce(col(Order.merchant_income), 0)),
        else_=0,
    )
    rows = (await session.exec(
        select(
            Order.payment_type_id, Order.channel_id,
            func.coalesce(func.sum(Order.realmoney), 0),
            func.coalesce(func.sum(profit_expr), 0),
        )
        .where(col(Order.status) == 1,
               col(Order.order_type) != ORDER_TYPE_TEST,
               col(Order.paid_at) >= today_start,
               col(Order.paid_at) < today_start + timedelta(days=1))
        .group_by(Order.payment_type_id, Order.channel_id)
    )).all()
    stat: dict = {"all": 0.0, "profit_all": 0.0, "paytype": {}, "channel": {}, "profit_paytype": {}}
    for tid, cid, money, profit in rows:
        m, p = _f(money), _f(profit)
        stat["all"] = round(stat["all"] + m, 2)
        stat["profit_all"] = round(stat["profit_all"] + p, 2)
        stat["paytype"][str(tid)] = round(_f(stat["paytype"].get(str(tid))) + m, 2)
        stat["channel"][str(cid)] = round(_f(stat["channel"].get(str(cid))) + m, 2)
        stat["profit_paytype"][str(tid)] = round(_f(stat["profit_paytype"].get(str(tid))) + p, 2)
    return stat


@router.get("", dependencies=[Depends(PermissionChecker("dashboard.view"))])
async def index(session: SessionDep, auth: AdminUser, days: int = 7):
    """首页统计总览：老后台 getcount 全量统计点（实时）+ 近 N 日趋势图。"""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    # ---- 近期成功率/成功笔数：单条条件聚合（窗口左移 1 分钟避开支付中/回调未到订单）----
    start60 = now - timedelta(minutes=60)
    start30 = now - timedelta(minutes=30)
    start10 = now - timedelta(minutes=10)
    win1_start = now - timedelta(minutes=2)
    win1_end = now - timedelta(minutes=1)
    stats_since = min(win1_start, today_start)

    paid = case((col(Order.status) == 1, 1), else_=0)
    row = (await session.exec(
        select(
            func.count().label("allnum"),
            func.sum(case((col(Order.created_at) >= today_start, paid), else_=0)).label("sucnum"),
            func.sum(case((col(Order.created_at) >= start60, paid), else_=0)).label("sucnum_60"),
            func.sum(case((col(Order.created_at) >= start30, paid), else_=0)).label("sucnum_30"),
            func.sum(case((col(Order.created_at) >= start10, paid), else_=0)).label("sucnum_10"),
            func.sum(case((and_(col(Order.created_at) >= win1_start, col(Order.created_at) < win1_end,
                                col(Order.status) == 1), 1), else_=0)).label("sucnum_1m"),
            func.sum(case((col(Order.created_at) >= start60, 1), else_=0)).label("allnum_60"),
            func.sum(case((col(Order.created_at) >= start30, 1), else_=0)).label("allnum_30"),
            func.sum(case((col(Order.created_at) >= start10, 1), else_=0)).label("allnum_10"),
            func.sum(case((and_(col(Order.created_at) >= win1_start, col(Order.created_at) < win1_end),
                           1), else_=0)).label("allnum_1m"),
        )
        .where(col(Order.created_at) >= stats_since,
               col(Order.order_type) != ORDER_TYPE_TEST)
    )).one()
    r = row._tuple() if hasattr(row, "_tuple") else row
    (allnum, sucnum, suc60, suc30, suc10, suc1m, all60, all30, all10, all1m) = [int(v or 0) for v in r]

    # ---- 预计收入（扣量订单）：今日 / 昨日（单条条件聚合，避免 4 次往返）----
    deducted_today = col(Order.created_at) >= today_start
    deducted_yesterday = and_(col(Order.created_at) >= yesterday_start,
                              col(Order.created_at) < today_start)
    today_deduct_money, today_deduct_count, yesterday_deduct_money, yesterday_deduct_count = (
        await session.exec(
            select(
                func.coalesce(func.sum(case((deducted_today, col(Order.money)), else_=0)), 0),
                func.coalesce(func.sum(case((deducted_today, 1), else_=0)), 0),
                func.coalesce(func.sum(case((deducted_yesterday, col(Order.money)), else_=0)), 0),
                func.coalesce(func.sum(case((deducted_yesterday, 1), else_=0)), 0),
            )
            .select_from(Order)
            .where(col(Order.is_deducted).is_(True), col(Order.created_at) >= yesterday_start)
        )
    ).one()

    # ---- 订单/商户计数（两条标量子查询，一次往返）----
    count1, count2 = (await session.exec(
        select(
            select(func.count()).select_from(Order)
            .where(col(Order.order_type) != ORDER_TYPE_TEST).scalar_subquery(),
            select(func.count()).select_from(Merchant)
            .where(col(Merchant.pid) != TEST_MERCHANT_PID).scalar_subquery(),
        )
    )).one()

    # ---- 支付方式 / 通道名称映射（仅启用）----
    enabled_types = (await session.exec(
        select(PaymentType).where(col(PaymentType.status) == 1).order_by(PaymentType.sort, PaymentType.id)
    )).all()
    paytype = {str(t.id): t.name for t in enabled_types}
    # 今日统计表头图标：id → 图标（与名称映射同口径）
    paytype_icon = {str(t.id): (t.icon or "") for t in enabled_types}
    channel = {str(c.id): c.name for c in (await session.exec(
        select(Channel).where(col(Channel.status) == 1).order_by(Channel.sort, Channel.id)
    )).all()}

    # ---- 重统计段（实时计算）----
    usermoney, settlemoney = (await session.exec(
        select(
            select(func.coalesce(func.sum(Merchant.balance), 0)).scalar_subquery(),
            select(func.coalesce(func.sum(Settlement.amount), 0))
            .where(col(Settlement.status) == 1).scalar_subquery(),  # 仅已打款（0 待处理 / 2 已拒绝不计）
        )
    )).one()
    order_today = await _today_stats(session, today_start)

    # 首页趋势图显隐（settings KV，默认开启；关闭后前端不渲染该卡片）
    settings = await get_all_settings(session)
    trend_enable = setting_enabled(settings.get("dashboard_trend_enable", True))

    # ---- 近 N 日趋势（图表用，保持既有口径：money=申报金额）----
    # 单条 GROUP BY 取代原来的「每天 2 条计数/求和」循环（30 天省 58 次往返）
    trend_days = max(1, min(days, 30))
    trend_start = (now - timedelta(days=trend_days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    trend_end = today_start + timedelta(days=1)
    day_col = func.date(Order.paid_at)
    rows = (await session.exec(
        select(
            day_col.label("d"),
            func.count().label("c"),
            func.coalesce(func.sum(Order.money), 0).label("m"),
        )
        .select_from(Order)
        .where(col(Order.status) == 1,
               col(Order.order_type) != ORDER_TYPE_TEST,
               col(Order.paid_at) >= trend_start,
               col(Order.paid_at) < trend_end)
        .group_by(day_col)
    )).all()
    by_day: dict[str, tuple[int, float]] = {}
    for d, c, m in rows:
        key = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        by_day[key] = (int(c or 0), _f(m))
    trend = []
    for i in range(trend_days - 1, -1, -1):
        day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
        cnt, money = by_day.get(day_start.strftime("%Y-%m-%d"), (0, 0.0))
        trend.append({"date": day_start.strftime("%Y-%m-%d"), "count": cnt, "money": money})

    # ---- 今日已支付 / 累计已支付（各 1 条条件聚合，避免 5 次往返）----
    # 利润=平台留存，扣量单补回未被支付给商户的 merchant_income（见 _today_stats 口径说明）
    today_profit_expr = func.coalesce(col(Order.profit), 0) + case(
        (col(Order.is_deducted) == True,  # noqa: E712
         func.coalesce(col(Order.merchant_income), 0)),
        else_=0,
    )
    today_paid_count, today_paid_money, today_profit = (await session.exec(
        select(
            func.count(),
            func.coalesce(func.sum(Order.money), 0),
            func.coalesce(func.sum(today_profit_expr), 0),
        )
        .select_from(Order)
        .where(col(Order.status) == 1, col(Order.order_type) != ORDER_TYPE_TEST,
               col(Order.paid_at) >= today_start)
    )).one()
    total_paid_count, total_paid_money = (await session.exec(
        select(
            func.count(),
            func.coalesce(func.sum(Order.money), 0),
        )
        .select_from(Order)
        .where(col(Order.status) == 1, col(Order.order_type) != ORDER_TYPE_TEST)
    )).one()

    return suc({
        # 既有趋势图字段（兼容保留）
        "today": {"count": int(today_paid_count), "money": _f(today_paid_money),
                  "profit": _f(today_profit)},
        "total": {"count": int(total_paid_count), "money": _f(total_paid_money),
                  "merchant_count": int(count2)},
        "trend": trend,
        "trend_enable": trend_enable,
        # ---- 首页统计点（实时）----
        "count1": int(count1),
        "count2": int(count2),
        "usermoney": round(_f(usermoney), 2),
        "settlemoney": round(_f(settlemoney), 2),
        "today_deduct_money": round(_f(today_deduct_money), 2),
        "today_deduct_count": int(today_deduct_count),
        "yesterday_deduct_money": round(_f(yesterday_deduct_money), 2),
        "yesterday_deduct_count": int(yesterday_deduct_count),
        "success_rate": _rate(sucnum, allnum),
        "success_rate_60m": _rate(suc60, all60),
        "success_rate_30m": _rate(suc30, all30),
        "success_rate_10m": _rate(suc10, all10),
        "success_rate_1m": _rate(suc1m, all1m),
        "sucnum_today": sucnum,
        "sucnum_60m": suc60,
        "sucnum_30m": suc30,
        "sucnum_10m": suc10,
        "sucnum_1m": suc1m,
        "paytype": paytype,
        "paytype_icon": paytype_icon,
        "channel": channel,
        "order_today": order_today,
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "admin": auth.user.username if auth.user else "",
    })


# /admin-api/index 旧入口兼容别名统一挂在 admin 根路由（admin/__init__.py），
# 这里不再重复注册 /dashboard/index。


# ============================================================
# 首页快捷配置：金额路由 / 通道限速
# ============================================================

@router.get("/config", dependencies=[Depends(PermissionChecker("dashboard.view"))])
async def home_config(session: SessionDep):
    """首页右侧快捷面板数据：支付方式、通道、金额路由与限速当前配置。"""
    pay_types = [{"id": t.id, "code": t.code, "name": t.name, "icon": t.icon or ""}
                 for t in (await session.exec(
        select(PaymentType).where(col(PaymentType.status) == 1).order_by(PaymentType.sort, PaymentType.id)
    )).all()]
    channels = [{"id": c.id, "name": c.name, "payment_type_id": c.payment_type_id}
                for c in (await session.exec(
        select(Channel).where(col(Channel.status) == 1).order_by(Channel.sort, Channel.id)
    )).all()]
    payroute = await get_payroute_config(session)
    ratelimit = await get_ratelimit_map(session)
    return suc({
        "pay_types": pay_types,
        "channels": channels,
        "payroute": {str(k): v for k, v in payroute.items()},
        "ratelimit": {str(k): v for k, v in ratelimit.items()},
        "ratelimit_last": await get_ratelimit_last(session),
    })


class PayrouteItem(BaseModel):
    type_id: int
    enable: bool = False
    min: str = "0.00"
    max: str = "0.00"
    channel: int = 0


class PayrouteRequest(PayrouteItem):
    pass


class PayrouteBatchRequest(BaseModel):
    items: list[PayrouteItem] = []


async def _check_route_item(session: AsyncSession, body: PayrouteItem) -> str | None:
    """校验单个金额路由配置，返回错误文案（None 表示通过）。

    固定通道必须属于该支付方式；金额区间两端都须大于 0 且 min <= max ——
    money_ok() 对任一 <=0 的区间直接判不生效，这里提前拦截，避免存下不会生效的配置。
    """
    pay_type = (await session.exec(
        select(PaymentType).where(col(PaymentType.id) == body.type_id)
    )).first()
    if not pay_type:
        return "支付方式不存在"

    try:
        minv = Decimal(str(body.min or 0).strip() or 0)
        maxv = Decimal(str(body.max or 0).strip() or 0)
    except InvalidOperation:
        return "金额格式错误"
    if minv < 0 or maxv < 0:
        return "金额不能为负数"

    channel_id = int(body.channel or 0)
    if channel_id > 0:
        ch = (await session.exec(
            select(Channel).where(col(Channel.id) == channel_id)
        )).first()
        if not ch or ch.payment_type_id != body.type_id:
            return "固定支付通道与支付方式不匹配"
        if minv <= 0 or maxv <= 0:
            return "启用金额路由时，最小/最大金额都须大于 0"
        if maxv < minv:
            return "最大金额不能小于最小金额"
    return None


def _route_payload(body: PayrouteItem) -> dict:
    return {
        "enable": 1 if body.enable else 0,
        "min": body.min, "max": body.max, "channel": int(body.channel or 0),
    }


@router.post("/payroute", dependencies=[Depends(PermissionChecker("channel.update"))])
async def save_payroute(body: PayrouteRequest, session: SessionDep):
    """保存单个支付方式的金额路由配置（首页「金额路由」面板）。"""
    msg = await _check_route_item(session, body)
    if msg:
        return err(-1, msg)
    await set_payroute_item(session, body.type_id, _route_payload(body))
    return suc("保存成功")


@router.post("/payroute/batch", dependencies=[Depends(PermissionChecker("channel.update"))])
async def save_payroute_batch(body: PayrouteBatchRequest, session: SessionDep):
    """批量保存金额路由（首页「金额路由」面板一次提交所有支付方式）。"""
    if not body.items:
        return err(-1, "没有需要保存的配置")
    if len({item.type_id for item in body.items}) != len(body.items):
        return err(-1, "支付方式重复")
    for item in body.items:
        msg = await _check_route_item(session, item)
        if msg:
            return err(-1, msg)
    await set_payroute_items(session, {item.type_id: _route_payload(item) for item in body.items})
    return suc("保存成功")


class RatelimitRequest(BaseModel):
    channel_id: int = 0   # 0=关闭所有通道限速
    value: int | None = None  # 次/分钟


@router.post("/ratelimit", dependencies=[Depends(PermissionChecker("channel.update"))])
async def save_ratelimit(body: RatelimitRequest, session: SessionDep):
    """保存通道限速；channel_id=0 一键关闭所有通道限速（首页「通道限速」面板）。"""
    if body.channel_id <= 0:
        await replace_ratelimit_map(session, {})
        await set_ratelimit_last(session, 0)
        return suc("已关闭所有通道限速")

    ch = (await session.exec(
        select(Channel).where(col(Channel.id) == body.channel_id)
    )).first()
    if not ch:
        return err(-1, "通道不存在")

    value = int(body.value or 0)
    if value < 0:
        return err(-1, "请输入正确的次数（非负整数）")
    await set_ratelimit_one(session, body.channel_id, value)
    await set_ratelimit_last(session, body.channel_id)
    return suc("保存成功")


class RatelimitLastRequest(BaseModel):
    channel_id: int = 0


@router.post("/ratelimit/last", dependencies=[Depends(PermissionChecker("channel.update"))])
async def save_ratelimit_last(body: RatelimitLastRequest, session: SessionDep):
    """记录限速面板最后选中通道（跨浏览器恢复用）。"""
    await set_ratelimit_last(session, int(body.channel_id or 0))
    return suc("ok")


@router.get("/ratelimit-stats", dependencies=[Depends(PermissionChecker("dashboard.view"))])
async def ratelimit_stats(session: SessionDep, cid: int = Query(0, ge=0)):
    """通道近期成交统计。

    窗口统一左移 1 分钟：进单量取 [2分钟前, 1分钟前)，成功率取「1 分钟前的最近 N 单」，
    避开正在支付/回调未到的订单被误算为失败。进单量按速率上限封顶。
    """
    ratelimit_map = await get_ratelimit_map(session)
    limit = int(ratelimit_map.get(cid, 0))
    if cid <= 0 or limit <= 0:
        return suc({"show": 0})

    now = datetime.now()
    win_start = now - timedelta(minutes=2)
    win_end = now - timedelta(minutes=1)

    allnum = int((await session.exec(
        select(func.count()).select_from(Order)
        .where(col(Order.channel_id) == cid,
               col(Order.order_type) != ORDER_TYPE_TEST,
               col(Order.created_at) >= win_start,
               col(Order.created_at) < win_end)
    )).one())
    allnum = min(allnum, limit)  # 展示值不超过当前设置的速率上限

    # 最近 N 单（1 分钟前之前）的成交情况；status 1=已支付 计为成功
    subq = (
        select(Order.status)
        .where(col(Order.channel_id) == cid,
               col(Order.order_type) != ORDER_TYPE_TEST,
               col(Order.created_at) < win_end)
        .order_by(col(Order.created_at).desc(), col(Order.trade_no).desc())
        .limit(limit)
        .subquery()
    )
    row = (await session.exec(
        select(
            func.count().label("total"),
            func.sum(case((col(subq.c.status) == 1, 1), else_=0)).label("success"),
        ).select_from(subq)
    )).one()
    total = int(row[0] or 0)
    success = int(row[1] or 0)
    rate = min(round(success / total * 100, 2), 100.0) if total > 0 else 0.0

    return suc({
        "show": 1, "limit": limit, "allnum": allnum,
        "total": total, "success": success, "rate": rate,
    })
