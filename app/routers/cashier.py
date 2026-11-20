"""收银台 API（/cashier）— 公开访问 + 限流，统一 {code, message, data} 包络。

- GET  /cashier/types?trade_no=    订单信息 + 可选支付方式（已拉起 jump/scheme 的附带
  pay_type/pay_info，收银台加载后自动跳转上游）
- POST /cashier/pay {trade_no,type} 按选定支付方式拉起支付
- GET  /cashier/status?trade_no=    支付状态轮询
（/cashier 页面本体见 routers/web.py）
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import select

from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.models import ChannelCode, Merchant, Order, PaymentType
from app.responses import err, suc_data
from app.routers.common.params import parse_bool
from app.services.channel import code_supports_type, list_available_pay_types
from app.services.order import (
    ORDER_PAID,
    build_signed_return_url,
    get_order_by_trade_no,
    order_remaining_seconds,
    process_payment,
)
from app.services.settings import get_all_settings

_log = logging.getLogger(__name__)

router = APIRouter()


def _is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad", "windows phone"))


async def _check_order_code(session, order) -> str:
    """订单绑定了通道编码时校验编码仍可用。

    编码被删除（或整体停用）时返回统一文案：不能落到下面「该支付方式未绑定」的分支，
    否则「配置被删」会被误报成「参数不匹配」，客服和买家都会往错误方向排查。
    """
    if not order.channel_code_id:
        return ""
    code = await session.get(ChannelCode, order.channel_code_id)
    if not code or code.status != 1:
        return "通道编码不存在或不可用"
    return ""


@router.get("/types")
async def order_pay_types(trade_no: str, session: SessionDep, cache: CacheDep,
                          _rl=rate_limit(times=60)):
    """订单信息 + 可选支付方式列表。"""
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在或已失效")
    if order.status == ORDER_PAID:
        return_url = await _signed_return_url(order, session, cache)
        return suc_data({"status": 1, "money": str(order.money), "subject": order.subject,
                         "types": [], "return_url": return_url})
    # 编码被删/停用时明确报错，不要静默返回空支付方式列表
    code_error = await _check_order_code(session, order)
    if code_error:
        return err(-1, code_error)
    types = await list_available_pay_types(session, cache, code_id=order.channel_code_id)
    data = {
        "status": 0,
        "money": str(order.money),
        "subject": order.subject,
        "types": types,
        "return_url": order.return_url or "",
    }
    # 已拉起过 jump/scheme 支付：附带 pay_type/pay_info，收银台加载后自动跳转上游
    # （仅待支付订单；过期/异常状态不跳）
    extra = order.extra or {}
    if order.status == 0 and extra.get("pay_type") in ("jump", "scheme") and extra.get("pay_info"):
        data["pay_type"] = extra["pay_type"]
        data["pay_info"] = extra["pay_info"]
    return suc_data(data)


@router.post("/pay")
async def launch_payment(request: Request, session: SessionDep, cache: CacheDep,
                         _rl=rate_limit(times=20)):
    """按选定支付方式拉起支付（收银台可切换支付方式后重新拉起）。"""
    try:
        body = await request.json()
        trade_no = str(body.get("trade_no", "") or "")
        type_code = str(body.get("type", "") or "")
        # 裸 bool() 会把字符串 "false"/"0" 判成真，导致每次都重新拉起上游；
        # 统一走 parse_bool（非法值 400 + 字段名）。
        refresh = parse_bool(body.get("refresh"), "refresh", default=False)
    except HTTPException:
        raise
    except Exception:
        return err(-1, "请求参数有误，请返回重新付款")
    if not trade_no or not type_code:
        return err(-1, "请求参数有误，请返回重新付款")

    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在")
    if order.status == ORDER_PAID:
        return err(-1, "订单已支付")
    if order.status != 0:
        return err(-1, "订单当前无法支付，请返回重新下单")

    pay_type = (await session.exec(
        select(PaymentType).where(PaymentType.code == type_code, PaymentType.status == 1)
    )).first()
    if not pay_type:
        return err(-1, "当前支付方式暂不可用，请选择其他支付方式")

    # 订单绑定了下游通道编码：编码被删/停用直接报编码不可用，其余情况只允许编码内已绑定的
    # 支付方式，否则后续必然拉起失败
    code_error = await _check_order_code(session, order)
    if code_error:
        return err(-1, code_error)
    if order.channel_code_id and not await code_supports_type(
        session, order.channel_code_id, pay_type.id
    ):
        return err(-1, "通道编码不支持当前支付方式")

    merchant = await session.get(Merchant, order.merchant_id)
    if not merchant:
        # 商户被删属于内部异常，买家只看到通用提示，细节写服务端日志
        _log.warning("收银台拉起支付失败：商户不存在 trade_no=%s merchant_id=%s",
                     order.trade_no, order.merchant_id)
        return err(-1, "支付服务暂不可用，请稍后重试")

    # 同一支付方式重复拉起：复用已落库的上游支付信息（与开放 API 下单口径一致），
    # 收银台刷新/回退不再每次都向上游新建付款会话；切换支付方式或 refresh=true 才重新拉起。
    extra = order.extra or {}
    stored_type = str(extra.get("pay_type") or "")
    stored_info = str(extra.get("pay_info") or "")
    if not refresh and order.payment_type_code == pay_type.code \
            and stored_type in ("jump", "scheme", "qrcode", "html") and stored_info:
        return suc_data({"pay_type": stored_type, "pay_info": stored_info,
                         "expires_in": order_remaining_seconds(order)})

    order.payment_type_id = pay_type.id
    order.payment_type_code = pay_type.code
    session.add(order)
    await session.commit()

    settings = await get_all_settings(session, cache)
    site = (settings.get("site_url") or str(request.base_url)).rstrip("/")
    flow = await process_payment(
        order, merchant, session, cache,
        is_mobile=_is_mobile(request),
        notify_base=site, return_base=site, site_url=site,
    )
    if not flow.ok:
        return err(-1, flow.error)
    return suc_data({"pay_type": flow.pay_type, "pay_info": flow.pay_info,
                     "expires_in": order_remaining_seconds(order)})


@router.get("/status")
async def order_status(trade_no: str, session: SessionDep, cache: CacheDep,
                       _rl=rate_limit(times=120)):
    """支付状态轮询。data.status：0 未支付 / 1 已支付（含带签名参数的回跳地址）。"""
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return err(404, "订单不存在")
    if order.status == ORDER_PAID:
        return suc_data({"status": 1, "return_url": await _signed_return_url(order, session, cache)})
    return suc_data({"status": 0, "return_url": ""})


async def _signed_return_url(order: Order, session: SessionDep, cache: CacheDep) -> str:
    """同步回跳地址带签名参数（return_url 携带验签参数）。"""
    if not order.return_url:
        return ""
    merchant = await session.get(Merchant, order.merchant_id)
    if not merchant:
        return order.return_url
    settings = await get_all_settings(session, cache)
    return build_signed_return_url(
        order, merchant, platform_rsa_key=settings.get("platform_rsa_private_key") or "",
    )
