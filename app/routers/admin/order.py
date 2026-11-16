"""admin 订单管理：多条件查询 / 详情 / 手动补单 / 重发通知 / 导出。"""
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlmodel import col, func, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import AdminUser, PermissionChecker
from app.lib.export import export_csv
from app.models import Merchant, Order
from app.responses import err, paginated_query_async, suc
from app.routers.common.params import parse_int
from app.services.notify import requeue_notify
from app.services.order import (
    get_order_by_trade_no,
    ORDER_PENDING,
    fmt_money,
    process_paid_order,
)
from app.worker import enqueue

router = APIRouter()


def _dump_order(order: Order, merchant_username: str = "") -> dict:
    return {
        "id": order.id,
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no,
        "api_trade_no": order.api_trade_no or "",
        "merchant_id": order.merchant_id,
        "merchant_username": merchant_username,
        "type": order.payment_type_code,
        "channel_id": order.channel_id,
        "plugin_name": order.plugin_name or "",
        "subject": order.subject,
        "money": str(order.money),
        "realmoney": str(order.realmoney or ""),
        "merchant_income": str(order.merchant_income or ""),
        "channel_cost": str(order.channel_cost or ""),
        "profit": str(order.profit or ""),
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
    """导出 CSV（最多 10000 行）。过滤条件来自 query 参数。"""
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
    rows = []
    for o in orders:
        rows.append({
            "trade_no": o.trade_no,
            "out_trade_no": o.out_trade_no,
            "merchant": name_map.get(o.merchant_id, ""),
            "type": o.payment_type_code,
            "subject": o.subject,
            "money": str(o.money),
            "realmoney": str(o.realmoney or ""),
            "merchant_income": str(o.merchant_income or ""),
            "profit": str(o.profit or ""),
            "status": status_text.get(o.status, str(o.status)),
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
            "paid_at": o.paid_at.strftime("%Y-%m-%d %H:%M:%S") if o.paid_at else "",
        })
    return export_csv(rows, f"orders_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv",
                      columns=["trade_no", "out_trade_no", "merchant", "type", "subject",
                               "money", "realmoney", "merchant_income", "profit",
                               "status", "created_at", "paid_at"])


@router.get("/{trade_no}", dependencies=[Depends(PermissionChecker("order.view"))])
async def order_detail(trade_no: str, session: SessionDep):
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在")
    merchant = await session.get(Merchant, order.merchant_id)
    return suc(_dump_order(order, merchant.username if merchant else ""))


@router.post("/refill", dependencies=[Depends(PermissionChecker("order.refill"))])
async def order_refill(session: SessionDep, cache: CacheDep, body: dict):
    """手动补单：跳过上游验证直接记账（金额用订单自身 realmoney）。

    扣量单（is_deducted=1）走扣量回补：补入账 + 补返佣 + 排队补发通知。
    """
    from app.services.order import undo_deduct

    trade_no = str(body.get("trade_no", "") or "")
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在")
    if order.is_deducted:
        if not await undo_deduct(session, order):
            return err(-1, "扣量补单失败")
        try:
            await enqueue("notify_merchant_job", trade_no=trade_no)
        except Exception:
            pass
        return suc("扣量单已回补入账并通知")
    result = await process_paid_order(
        trade_no, order.api_trade_no or "MANUAL",
        order.realmoney if order.realmoney is not None else order.money,
        order.buyer_id or "manual", session, cache,
    )
    if result.status in ("paid", "duplicate"):
        if result.status == "paid":
            try:
                await enqueue("notify_merchant_job", trade_no=trade_no)
            except Exception:
                pass
        return suc("补单成功")
    if result.status == "mismatch":
        return err(-1, "金额校验失败")
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
