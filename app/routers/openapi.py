"""商户开放 API — 根路径资源风格，统一 {code, message, data} 包络。

认证：pid + RSA2 签名（签名在 JSON body 参数内，支付宝风格）；
timestamp 必填、±300s 防重放；GET 查单同一组参数放 query。
响应签名不做：请求认证已覆盖请求方向，响应回传在同一 TLS 连接内
（安全关键通道是 Webhook，已由平台私钥签名）。

端点：
- POST /api/orders                  创建支付订单
- GET  /api/orders/{trade_no}       查询订单（path 资源 + query 签名认证）
- POST /api/orders/{trade_no}/refund 发起退款（api_refund_enable 开关）
- GET  /api/refunds/{refund_no}     查询退款单（?out_refund_no= 兜底）
"""
import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.api_deps import ApiUser
from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit_setting
from app.lib.linktoken import cashier_url as build_cashier_url
from app.services.order import (
    ORDER_PENDING,
    PayFlowResult,
    create_order,
    get_order_by_trade_no,
    order_query_payload,
    order_remaining_seconds,
    process_payment,
)
from app.services.refund import create_refund, get_refund, refund_query_payload
from app.services.settings import get_setting, setting_enabled

_log = logging.getLogger(__name__)

router = APIRouter()


def api_response(*, code: int = 0, message: str = "success",
                 data=None, http_status: int = 200) -> JSONResponse:
    """开放 API 业务响应：纯 {code, message, data} 包络（无签名）。"""
    payload: dict = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return JSONResponse(status_code=http_status, content=payload)


def _is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad", "windows phone"))


def _request_host(request: Request) -> str:
    """从 Referer/Origin 解析来源 host（域名白名单校验用），无则空串。"""
    from urllib.parse import urlparse

    for header in ("referer", "origin"):
        raw = request.headers.get(header, "")
        if raw:
            try:
                return urlparse(raw).hostname or ""
            except Exception:
                continue
    return ""


@router.post("/orders")
async def create_payment_order(request: Request, session: SessionDep, cache: CacheDep,
                               api: ApiUser,
                               _rl=rate_limit_setting("api_order_rate_limit", 60)):
    """创建支付订单。

    Body（JSON）：pid, type(alipay/wxpay/...), out_trade_no, notify_url, return_url,
    name(商品名), money, param(可选), timestamp, sign, sign_type=RSA2

    成功 data：{trade_no, out_trade_no, pay_type(jump/qrcode/scheme/html),
    payurl|qrcode|urlscheme|html, cashier_url, expires_in}。
    同一 out_trade_no 重复请求幂等返回原订单（trade_no 不变）。
    拉起上游失败时返回错误，订单保留待支付（trade_no 有效，可重试或走收银台）。
    """
    params = dict(api.params)
    params.setdefault("client_ip", request.client.host if request.client else "")
    # 支付域名白名单来源：Referer/Origin 的 host（商户端服务间调用通常无 Referer，
    # 白名单未配置时不校验）
    request_host = _request_host(request)
    try:
        order = await create_order(params, session, cache, request_host=request_host)
    except ValueError as e:
        return api_response(code=-1, message=str(e), http_status=400)

    settings = await get_setting(session, "site_url", "", cache=cache)
    site = (str(settings) or str(request.base_url)).rstrip("/")

    # 幂等重提交：已拉起过的待支付单直接返回落库的支付信息，
    # 不重复请求上游（随机金额单重复拉起虽不再重随机 realmoney，
    # 但上游会生成新付款会话，旧链接仍有效时徒增副作用）
    extra = order.extra or {}
    stored_type = str(extra.get("pay_type") or "")
    stored_info = str(extra.get("pay_info") or "")
    if order.status == ORDER_PENDING and stored_type in ("jump", "scheme", "qrcode", "html") \
            and stored_info:
        flow = PayFlowResult(ok=True, pay_type=stored_type, pay_info=stored_info,
                             expires_in=order_remaining_seconds(order))
    else:
        flow = await process_payment(
            order, api.merchant, session, cache,
            is_mobile=_is_mobile(request),
            notify_base=site, return_base=site, site_url=site,
        )
    if not flow.ok:
        # 拉起失败：订单保留待支付，商户可用 out_trade_no 重查或稍后重试
        return api_response(code=-1, message=flow.error, http_status=400)

    data = {
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no,
        "pay_type": flow.pay_type,
        "cashier_url": build_cashier_url(site, order.trade_no),
        "expires_in": order_remaining_seconds(order),
    }
    if flow.pay_type == "jump":
        data["payurl"] = flow.pay_info
    elif flow.pay_type == "qrcode":
        data["qrcode"] = flow.pay_info
    elif flow.pay_type == "scheme":
        data["urlscheme"] = flow.pay_info
    elif flow.pay_type == "html":
        data["html"] = flow.pay_info
    return api_response(data=data)


@router.get("/orders/{trade_no}")
async def query_payment_order(trade_no: str, session: SessionDep, cache: CacheDep, api: ApiUser):
    """查询订单。data.status：0 未支付 / 1 已支付；refund_status：NONE/PARTIAL/FULL。"""
    order = await get_order_by_trade_no(session, trade_no)
    if not order or order.merchant_id != api.merchant.id:
        return api_response(code=404, message="订单不存在", http_status=400)
    return api_response(data=await order_query_payload(order, session))


@router.post("/orders/{trade_no}/refund")
async def create_order_refund(trade_no: str, session: SessionDep, cache: CacheDep, api: ApiUser):
    """发起退款。

    Body（JSON）：refund_amount(可选，缺省=剩余可退全额), out_refund_no(可选，幂等键),
    reason(可选), timestamp, sign, sign_type=RSA2。
    同步退款模型：插件返回即终态；上游失败报错不落库零副作用。
    """
    if not setting_enabled(await get_setting(session, "api_refund_enable", True, cache=cache)):
        return api_response(code=-1, message="退款接口未开放", http_status=400)

    params = api.params
    refund_amount = params.get("refund_amount")
    if refund_amount is not None and str(refund_amount).strip() != "":
        try:
            refund_amount = Decimal(str(refund_amount))
        except (InvalidOperation, ValueError):
            return api_response(code=-1, message="退款金额格式错误", http_status=400)
    else:
        refund_amount = None

    try:
        refund = await create_refund(
            session, api.merchant, trade_no,
            refund_amount=refund_amount,
            out_refund_no=str(params.get("out_refund_no", "") or ""),
            reason=str(params.get("reason", "") or ""),
        )
    except ValueError as e:
        return api_response(code=-1, message=str(e), http_status=400)
    return api_response(data=refund_query_payload(refund))


@router.get("/refunds/{refund_no}")
async def query_refund(refund_no: str, session: SessionDep, cache: CacheDep, api: ApiUser,
                       out_refund_no: str = ""):
    """查询退款单（refund_no 平台退款单号；?out_refund_no= 商户退款号兜底）。"""
    refund = await get_refund(session, api.merchant, refund_no=refund_no,
                              out_refund_no=out_refund_no)
    if not refund:
        return api_response(code=404, message="退款单不存在", http_status=400)
    return api_response(data=refund_query_payload(refund))
