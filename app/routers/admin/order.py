"""admin 订单管理：多条件查询 / 详情 / 手动补单 / 重发通知 / 导出。"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import col, func, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import AdminUser, PermissionChecker
from app.lib.export import export_xlsx
from app.models import Merchant, Order
from app.responses import err, paginated_query_async, suc
from app.routers.common.params import parse_int
from app.services.notify import requeue_notify
from app.services.order import (
    get_order_by_trade_no,
    ORDER_PENDING,
    fmt_money,
    money_text,
    payment_type_icon_map,
    payment_type_name_map,
    process_paid_order,
)
from app.worker import enqueue

router = APIRouter()


def _dump_order(order: Order, merchant_username: str = "", type_name: str = "",
                type_icon: str = "") -> dict:
    return {
        "id": order.id,
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no,
        "api_trade_no": order.api_trade_no or "",
        "merchant_id": order.merchant_id,
        "merchant_username": merchant_username,
        # 0=普通收款 1=余额充值 2=测试单（前端据此显示「测试」标记并禁用通知等操作）
        "order_type": order.order_type,
        "type": order.payment_type_code,
        "type_name": type_name,
        "type_icon": type_icon,
        "channel_id": order.channel_id,
        # 下游通道编码：下单时下游传的编码（留痕，编码改名/删除后仍是当时的值）
        "channel_code": order.channel_code or "",
        "channel_code_id": order.channel_code_id,
        "plugin_name": order.plugin_name or "",
        "subject": order.subject,
        "money": str(order.money),
        "realmoney": money_text(order.realmoney),
        "merchant_income": money_text(order.merchant_income),
        "channel_cost": money_text(order.channel_cost),
        "profit": money_text(order.profit),
        "notify_url": order.notify_url or "",
        "return_url": order.return_url or "",
        "param": order.param or "",
        "status": order.status,
        "is_deducted": bool(order.is_deducted),
        "notify_status": order.notify_status,
        "notify_count": order.notify_count,
        "next_notify_at": order.next_notify_at.strftime("%Y-%m-%d %H:%M:%S") if order.next_notify_at else "",
        "paid_at": order.paid_at.strftime("%Y-%m-%d %H:%M:%S") if order.paid_at else "",
        "expire_at": order.expire_at.strftime("%Y-%m-%d %H:%M:%S") if order.expire_at else "",
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
        "client_ip": order.client_ip or "",
        "buyer_id": order.buyer_id or "",
    }


def _build_query(body: dict):
    query = select(Order).order_by(col(Order.id).desc())
    # 订单类型：默认不筛类型（全部类型都返回，测试单默认可见）；显式传 0/1/2 按类型筛。
    ot = str(body.get("order_type", "") or "").strip()
    if ot in ("0", "1", "2"):
        query = query.where(Order.order_type == int(ot))
    if body.get("trade_no"):
        query = query.where(Order.trade_no == body["trade_no"])
    if body.get("out_trade_no"):
        query = query.where(Order.out_trade_no == body["out_trade_no"])
    if body.get("merchant_id"):
        # 商户列表「订单」快捷跳转：/trade/orders?merchant_id=xx
        query = query.where(Order.merchant_id == parse_int(body["merchant_id"], "merchant_id"))
    if body.get("channel_id"):
        # 通道列表「订单」快捷跳转：/trade/orders?channel_id=xx
        query = query.where(Order.channel_id == parse_int(body["channel_id"], "channel_id"))
    if body.get("channel_code_id"):
        # 下游通道编码列表「订单」快捷跳转：/trade/orders?channel_code_id=xx
        query = query.where(Order.channel_code_id == parse_int(body["channel_code_id"], "channel_code_id"))
    if body.get("channel_code"):
        query = query.where(Order.channel_code == str(body["channel_code"]).strip().lower())
    if body.get("status") is not None and body.get("status") != "":
        query = query.where(Order.status == parse_int(body["status"], "status"))
    if body.get("type"):
        query = query.where(Order.payment_type_code == body["type"])
    if body.get("notify_status") is not None and body.get("notify_status") != "":
        query = query.where(Order.notify_status == parse_int(body["notify_status"], "notify_status"))
    addtime = body.get("addtime") or ""
    if "," in addtime:
        start, end = addtime.split(",", 1)
        if start.strip():
            query = query.where(col(Order.created_at) >= start.strip())
        if end.strip():
            query = query.where(col(Order.created_at) <= end.strip() + " 23:59:59")
    return query


@router.post("/list", dependencies=[Depends(PermissionChecker("order.view"))])
async def order_list(session: SessionDep, page: int = Query(1, ge=1),
                     per_page: int = Query(20, ge=1, le=100), body: dict | None = None):
    body = body or {}
    # DataList 把 page/per_page 放在 body 里，此处兜底兼容
    page = parse_int(body.get("page", page), "page", default=page, minimum=1)
    per_page = parse_int(body.get("per_page", per_page), "per_page",
                         default=per_page, minimum=1, maximum=100)
    query = _build_query(body)
    data = await paginated_query_async(session, query, page, per_page,
                                       dump_fn=lambda o: _dump_order(o))
    # 补支付方式显示名（与商户端/代理端共用 services.order.payment_type_name_map，避免各写一份）
    type_names = await payment_type_name_map(session)
    type_icons = await payment_type_icon_map(session)
    for it in data["items"]:
        it["type_name"] = type_names.get(it["type"], "")
        it["type_icon"] = type_icons.get(it["type"], "")
    # 补商户名
    merchant_ids = {it["merchant_id"] for it in data["items"]}
    if merchant_ids:
        merchants = (await session.exec(
            select(Merchant).where(col(Merchant.id).in_(merchant_ids))
        )).all()
        name_map = {m.id: m.username for m in merchants}
        for it in data["items"]:
            it["merchant_username"] = name_map.get(it["merchant_id"], "")
    # 汇总（必须引用子查询列：直接引用 Order.money 会把 orders 再放进 FROM，产生笛卡尔积）
    subq = _build_query(body).order_by(None).subquery()
    total_money = (await session.exec(
        select(func.coalesce(func.sum(subq.c.money), 0)).select_from(subq)
    )).one()
    data["summary"] = {"money": str(Decimal(str(total_money)))}
    return suc(data)


@router.get("/export", dependencies=[Depends(PermissionChecker("order.export"))])
async def order_export(session: SessionDep, request: Request):
    """导出 Excel(xlsx，最多 10000 行）。过滤条件来自 query 参数。"""
    body = {k: v for k, v in request.query_params.items() if v not in ("", None)}
    query = _build_query(body).limit(10000)
    orders = (await session.exec(query)).all()
    merchant_ids = {o.merchant_id for o in orders}
    name_map = {}
    if merchant_ids:
        merchants = (await session.exec(
            select(Merchant).where(col(Merchant.id).in_(merchant_ids))
        )).all()
        name_map = {m.id: m.username for m in merchants}

    status_text = {0: "待支付", 1: "已支付", 2: "已退款", 3: "已过期"}
    type_text = {0: "正式收款", 1: "余额充值", 2: "测试单"}
    rows = []
    for o in orders:
        rows.append({
            "trade_no": o.trade_no,
            "out_trade_no": o.out_trade_no,
            "merchant": name_map.get(o.merchant_id, ""),
            "order_type": type_text.get(o.order_type, str(o.order_type)),
            "type": o.payment_type_code,
            "subject": o.subject,
            "money": str(o.money),
            "realmoney": money_text(o.realmoney),
            "merchant_income": money_text(o.merchant_income),
            "profit": money_text(o.profit),
            "status": status_text.get(o.status, str(o.status)),
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
            "paid_at": o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else "",
        })
    return export_xlsx(rows, f"orders_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                      columns=["trade_no", "out_trade_no", "merchant", "order_type", "type",
                               "subject", "money", "realmoney", "merchant_income", "profit",
                               "status", "created_at", "paid_at"])


@router.get("/{trade_no}", dependencies=[Depends(PermissionChecker("order.view"))])
async def order_detail(trade_no: str, session: SessionDep):
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在")
    merchant = await session.get(Merchant, order.merchant_id)
    type_names = await payment_type_name_map(session)
    type_icons = await payment_type_icon_map(session)
    return suc(_dump_order(order, merchant.username if merchant else "",
                           type_names.get(order.payment_type_code, ""),
                           type_icons.get(order.payment_type_code, "")))


@router.post("/refill", dependencies=[Depends(PermissionChecker("order.refill"))])
async def order_refill(session: SessionDep, cache: CacheDep, request: Request, auth: AdminUser,
                       body: dict):
    """手动补单：跳过上游验证直接记账（金额用订单自身 realmoney）。

    扣量单（is_deducted=1）走扣量回补：补入账 + 补返佣 + 排队补发通知。
    过期单（status=3）走**受控回补路径**：管理员已确认上游确实收到款，
    因此不受 late_pay_backfill_hours 窗口限制（P0-4）；返回值里会标明是过期回补。
    """
    from app.services.order import ORDER_EXPIRED, ORDER_PAID, undo_deduct

    trade_no = str(body.get("trade_no", "") or "")
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在")
    if order.status == ORDER_PAID and not order.is_deducted:
        return suc("订单已支付，无需补单")
    if order.is_deducted:
        if not await undo_deduct(session, order):
            return err(-1, "扣量补单失败")
        try:
            await enqueue("notify_merchant_job", trade_no=trade_no)
        except Exception:
            pass
        return suc("扣量单已回补入账并通知")
    was_expired = order.status == ORDER_EXPIRED
    result = await process_paid_order(
        trade_no, order.api_trade_no or "MANUAL",
        order.realmoney if order.realmoney is not None else order.money,
        order.buyer_id or "manual", session, cache,
        allow_expired=True,  # 人工确认过的受控回补
    )
    if result.status in ("paid", "duplicate"):
        if result.status == "paid" and result.should_notify:
            try:
                await enqueue("notify_merchant_job", trade_no=trade_no)
            except Exception:
                pass
        return suc("过期单回补成功（请核对上游收款凭证）" if was_expired else "补单成功")
    if result.status == "mismatch":
        return err(-1, "金额校验失败")
    if result.status == "invalid":
        return err(-1, "订单状态不可补单，请核对订单状态后重试")
    return err(-1, _refill_error_text(result.status))


# 补单失败给管理员看的文案：内部状态枚举（invalid/lock_failed…）不直接外露
_REFILL_ERROR_TEXT = {
    "not_found": "订单不存在",
    "lock_failed": "订单正在处理中，请稍后重试",
    "invalid": "订单当前状态不可补单（仅待支付订单可补单），请刷新后重试",
    "mismatch": "金额校验失败",
}


def _refill_error_text(status: str) -> str:
    return _REFILL_ERROR_TEXT.get(status, "补单失败，请稍后重试（订单状态可能已变更）")


@router.post("/notify", dependencies=[Depends(PermissionChecker("order.notify"))])
async def order_notify(session: SessionDep, body: dict):
    """重发商户通知。"""
    trade_no = str(body.get("trade_no", "") or "")
    if not await requeue_notify(session, trade_no):
        return err(-1, "订单不存在或未支付")
    try:
        await enqueue("notify_merchant_job", trade_no=trade_no)
    except Exception:
        pass
    return suc("已触发重发通知")
