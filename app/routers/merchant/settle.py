"""商户端结算：余额/收款信息、费率预览、申请结算、结算记录 + 充值。

充值走收银台闭环：创建充值单（order_type=1）→ /cashier 支付 → 支付成功全额入账。
"""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlmodel import col, func, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import MerchantUser
from app.lib.linktoken import cashier_url as build_cashier_url
from app.models import Order, PaymentType, Settlement
from app.responses import err, paginate, suc
from app.services.balance import change_merchant_balance, dmoney
from app.services.channel import list_available_pay_types
from app.services.order import create_order, process_payment
from app.services.settings import get_all_settings, get_setting
from app.services.settle import SETTLE_TYPES, apply_settlement, settle_fee_preview

router = APIRouter()


@router.get("/info")
async def settle_info(session: SessionDep, auth: MerchantUser):
    """余额 + 收款信息 + 待处理结算数 + 费率/起结/预留/可提预览。"""
    m = auth.user
    pending = (await session.exec(
        select(func.count()).select_from(Settlement).where(
            Settlement.merchant_id == m.id, Settlement.status == 0)
    )).one()
    preview = await settle_fee_preview(session, m)
    return suc({
        "balance": str(m.balance),
        "frozen_balance": str(m.frozen_balance),
        "settle_account": m.settle_account or "",
        "settle_name": m.settle_name or "",
        "settle_type": m.settle_type or 1,
        "settle_type_name": SETTLE_TYPES.get(m.settle_type or 1, "支付宝"),
        "pending_count": int(pending),
        "auto_settle": bool(m.auto_settle),
        # 充值可选支付方式：启用且有可用通道（与收银台同口径）
        "types": await list_available_pay_types(session),
        **preview,
    })


class ApplyRequest(BaseModel):
    amount: str


@router.post("/apply")
async def apply(body: ApplyRequest, session: SessionDep, auth: MerchantUser):
    """发起结算申请（扣余额）。"""
    settle, error = await apply_settlement(session, auth.user, body.amount)
    if error:
        return err(-1, error)
    return suc({"settle_no": settle.settle_no, "amount": str(settle.amount)})


class AutoSettleRequest(BaseModel):
    auto_settle: bool


@router.post("/auto")
async def set_auto_settle(body: AutoSettleRequest, session: SessionDep, auth: MerchantUser):
    """自动结算开关：开启后 cron 对可提余额达标的商户自动发起结算。"""
    m = auth.user
    m.auto_settle = body.auto_settle
    session.add(m)
    await session.commit()
    return suc("success")


# ============================================================
# 余额充值（order_type=1 充值单，收银台支付，全额入账）
# ============================================================

class RechargeRequest(BaseModel):
    amount: str
    type: str  # 支付方式 code


@router.post("/recharge")
async def recharge(body: RechargeRequest, request: Request, session: SessionDep, cache: CacheDep,
                   auth: MerchantUser):
    enabled = await get_setting(session, "recharge_enable", True)
    if not enabled:
        return err(-1, "余额充值未开启，请联系管理员")

    try:
        amount = dmoney(body.amount)
    except Exception:
        return err(-1, "金额格式错误")
    if amount < 1:
        return err(-1, "充值金额至少 1 元")

    # 充值单号：RC + 商户pid + 时间戳 + 随机（同一秒重复提交也会因唯一键天然分开）
    import random
    out_trade_no = f"RC{auth.user.pid}{datetime_now_str()}{random.randint(1000, 9999)}"

    try:
        order = await create_order(
            {
                "pid": auth.user.pid,
                "out_trade_no": out_trade_no,
                "name": "余额充值",
                "money": str(amount),
                "type": body.type,
                "notify_url": "",
                "return_url": "",
                "client_ip": request.client.host if request.client else "",
            },
            session, cache, order_type=1,
        )
    except ValueError as e:
        return err(-1, str(e))

    settings = await get_all_settings(session, cache)
    site = (settings.get("site_url") or str(request.base_url)).rstrip("/")
    flow = await process_payment(order, auth.user, session, cache, site_url=site,
                                 notify_base=site, return_base=site)
    if not flow.ok:
        return err(-1, flow.error)
    return suc({"trade_no": order.trade_no, "cashier_url": build_cashier_url(site, order.trade_no)})


def datetime_now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%y%m%d%H%M%S")
