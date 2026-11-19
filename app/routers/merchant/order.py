"""商户端订单查询（merchant_id 数据隔离）+ 统计汇总 + Excel 导出 + 手动重发通知。

列表默认仅展示收款单（order_type=0），余额充值单在资金流水/充值入口体现。
扣量单（is_deducted）对商户侧一律显示为未支付：状态遮蔽、无完成时间、不计入统计
（商户面板口径）。
"""
from decimal import Decimal

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import or_
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import MerchantUser
from app.lib.export import export_xlsx
from app.models import Order
from app.responses import err, paginate, suc
from app.routers.common.params import parse_int
from app.services.order import (
    ORDER_PAID,
    ORDER_REFUNDED,
    fmt_money,
    get_order_by_trade_no,
    payment_type_icon_map,
    payment_type_name_map,
    refundable_base,
)
from app.worker import enqueue

router = APIRouter()


def _dump_order(o: Order, type_names: dict[str, str] | None = None,
                type_icons: dict[str, str] | None = None) -> dict:
    deducted = bool(o.is_deducted)
    type_code = o.payment_type_code or ""
    return {
        "trade_no": o.trade_no,
        "out_trade_no": o.out_trade_no,
        # 0=普通收款 2=测试单（商户列表默认只看普通收款，测试单手动筛）
        "order_type": o.order_type,
        "type": type_code,
        "type_name": (type_names or {}).get(type_code, type_code),
        "type_icon": (type_icons or {}).get(type_code, ""),
        "subject": o.subject,
        "money": str(o.money),
        "realmoney": "" if deducted else str(o.realmoney or ""),
        "merchant_income": "" if deducted else str(o.merchant_income or ""),
        "status": 0 if deducted else o.status,
        "notify_status": 0 if deducted else o.notify_status,
        "agent_id": o.agent_id,
        "param": o.param or "",
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
        "paid_at": "" if deducted else (o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else ""),
    }


def _build_conditions(auth: MerchantUser, body: dict, for_summary: bool = False) -> list:
    """列表/统计/导出共用的过滤条件（恒定 merchant_id 隔离 + 收款单/测试单）。

    for_summary=True 时排除扣量单（统计口径不含被抽单订单）；
    列表场景保留扣量单但状态筛选按「商户视角」处理：筛 1=已支付 时排除扣量单，
    筛 0=未支付 时把扣量单（商户视角的未支付）也纳入。

    订单类型：默认只看普通收款单（order_type=0）；order_type=2 时只看测试单。
    充值单在资金流水/充值入口体现，不在订单列表出现。
    """
    order_type = 2 if str(body.get("order_type", "") or "") == "2" else 0
    conditions = [Order.merchant_id == auth.user.id, Order.order_type == order_type]
    if body.get("trade_no"):
        conditions.append(Order.trade_no == body["trade_no"])
    if body.get("out_trade_no"):
        conditions.append(Order.out_trade_no == body["out_trade_no"])
    if body.get("status") is not None and body.get("status") != "":
        status = parse_int(body["status"], "status")
        if status == 1:
            conditions.append(col(Order.status) == 1)
            conditions.append(col(Order.is_deducted) == False)  # noqa: E712
        elif status == 0:
            conditions.append(
                or_(col(Order.status) == 0, col(Order.is_deducted) == True)  # noqa: E712
            )
        else:
            conditions.append(Order.status == status)
    if body.get("type"):
        conditions.append(Order.payment_type_code == body["type"])
    if body.get("agent_id") is not None and body.get("agent_id") != "":
        conditions.append(Order.agent_id == parse_int(body["agent_id"], "agent_id"))
    if body.get("subject"):
        conditions.append(col(Order.subject).contains(str(body["subject"])))
    addtime = body.get("addtime") or ""
    if "," in addtime:
        start, end = addtime.split(",", 1)
        if start.strip():
            conditions.append(col(Order.created_at) >= start.strip())
        if end.strip():
            conditions.append(col(Order.created_at) <= end.strip() + " 23:59:59")
    if for_summary:
        conditions.append(col(Order.is_deducted) == False)  # noqa: E712
    return conditions


def _build_query(auth: MerchantUser, body: dict):
    query = select(Order).where(*_build_conditions(auth, body)).order_by(col(Order.id).desc())
    return query


@router.post("/list")
async def order_list(session: SessionDep, auth: MerchantUser,
                     page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                     body: dict | None = None):
    body = body or {}
    query = _build_query(auth, body)
    total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()

    # 按当前筛选条件的统计汇总（不含扣量单）
    conditions = _build_conditions(auth, body, for_summary=True)
    # 汇总口径：默认只统计已支付收款单（未支付不计收入，已退款余额已冲回）；
    # 显式按状态筛选时按所选状态统计，便于对账。
    if body.get("status") in (None, ""):
        conditions.append(col(Order.status) == ORDER_PAID)
    count_q = select(
        func.count(), func.coalesce(func.sum(Order.money), 0),
        func.coalesce(func.sum(Order.merchant_income), 0),
    ).select_from(Order).where(*conditions)
    count, money_sum, income_sum = (await session.exec(count_q)).one()

    type_names = await payment_type_name_map(session)
    type_icons = await payment_type_icon_map(session)
    return suc(paginate(
        items=[_dump_order(o, type_names, type_icons) for o in items], total=total,
        page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
        summary={"count": int(count), "money": str(money_sum), "income": str(income_sum)},
    ))


@router.post("/export")
async def order_export(session: SessionDep, auth: MerchantUser, body: dict | None = None):
    """Excel(xlsx) 导出当前筛选结果（上限 1 万行）。"""
    body = body or {}
    query = _build_query(auth, body).limit(10000)
    items = (await session.exec(query)).all()
    type_names = await payment_type_name_map(session)
    type_icons = await payment_type_icon_map(session)
    status_text = {0: "待支付", 1: "已支付", 2: "已退款", 3: "已过期"}
    rows = []
    for o in items:
        row = _dump_order(o, type_names, type_icons)
        # 导出文件里支付方式/状态给人类可读文案，避免导出 alipay / 2 这类裸 code
        row["type"] = row["type_name"]
        row["order_type"] = "测试单" if o.order_type == 2 else "正式收款"
        row["status"] = status_text.get(row["status"], str(row["status"]))
        rows.append(row)
    return export_xlsx(
        items=rows,
        filename=f"orders-{auth.user.pid}.xlsx",
        columns=[
            ("trade_no", "平台订单号"), ("out_trade_no", "商户订单号"),
            ("order_type", "订单类型"),
            ("type", "支付方式"), ("subject", "商品名称"),
            ("money", "金额"), ("realmoney", "实付金额"), ("merchant_income", "到账金额"),
            ("status", "状态"),
            ("notify_status", "通知状态(-1失败/0成功/1+重试中)"),
            ("agent_id", "代理ID"), ("param", "业务扩展参数"),
            ("created_at", "创建时间"), ("paid_at", "支付时间"),
        ],
    )


@router.get("/{trade_no}")
async def order_detail(trade_no: str, session: SessionDep, auth: MerchantUser):
    """商户面板订单详情：字段与列表行同形（subject/created_at/paid_at/status），
    另补退款状态与累计退款金额，供详情弹窗展示。

    不复用开放 API 的 order_query_payload（那是 name/addtime/endtime 的对外契约，
    且状态对商户下游只区分已付/未付），避免面板与接口口径互相牵连。
    """
    order = await get_order_by_trade_no(session, trade_no)
    if not order or order.merchant_id != auth.user.id:
        return err(404, "订单不存在")
    payload = _dump_order(order, await payment_type_name_map(session),
                          await payment_type_icon_map(session))
    payload["api_trade_no"] = order.api_trade_no or ""
    payload["channel_id"] = order.channel_id

    paid = order.status in (ORDER_PAID, ORDER_REFUNDED) and not order.is_deducted
    refunded = Decimal("0.00")
    if paid:
        from app.services.refund import refunded_amount
        refunded = await refunded_amount(session, order.trade_no)
    if not paid or refunded <= 0:
        refund_status, refunded_str = "NONE", "0.00"
    elif order.status == ORDER_REFUNDED or refunded >= refundable_base(order):
        refund_status, refunded_str = "FULL", fmt_money(refunded)
    else:
        refund_status, refunded_str = "PARTIAL", fmt_money(refunded)
    payload["refund_status"] = refund_status
    payload["refunded_amount"] = refunded_str
    return suc(payload)


@router.post("/{trade_no}/notify")
async def order_notify(trade_no: str, session: SessionDep, auth: MerchantUser):
    """手动重发通知。"""
    from app.services.notify import requeue_notify
    order = await get_order_by_trade_no(session, trade_no)
    if not order or order.merchant_id != auth.user.id:
        return err(404, "订单不存在")
    if order.is_deducted:
        return err(-1, "订单未支付，无法通知")
    if order.order_type == 2:
        return err(-1, "测试单不发送商户通知")
    if not await requeue_notify(session, trade_no):
        return err(-1, "订单未支付，无法通知")
    try:
        await enqueue("notify_merchant_job", trade_no=trade_no)
    except Exception:
        pass
    return suc("已触发重发通知")
