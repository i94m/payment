"""易支付上游通道插件。

对接上游网关接口：
- appswitch=1：POST {appurl}mapi.php 下单，拿 payurl/qrcode/urlscheme
- appswitch=0：跳转 {appurl}submit.php?签名参数
- 回调：GET 带签名参数，验签 + TRADE_SUCCESS 判成功，应答 success/fail
- 查单：GET {appurl}api.php?act=order&pid=&key=&out_trade_no=
- 退款：GET {appurl}api.php?act=refund

签名规则：排除 sign/sign_type 与空字符串，key 升序拼串 + appkey，MD5 小写。
金额校验不在此处做（services/order.process_paid_order 统一比对 realmoney）。
"""
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from app.services.http_client import get_http_client
from app.services.plugin_interfaces import (
    BasePayment,
    NotifyVerdict,
    PaymentContext,
    PaymentResult,
    PluginMeta,
    QueryResult,
)

_log = logging.getLogger(__name__)


def _sign_content_epay(data: dict) -> str:
    """签名原文：排除 sign/sign_type 与空串（注意不排除 "0"）。"""
    keys = sorted(
        k for k, v in data.items()
        if k not in ("sign", "sign_type")
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    return "&".join(f"{k}={data[k]}" for k in keys)


def _make_sign(data: dict, appkey: str) -> str:
    import hashlib
    return hashlib.md5((_sign_content_epay(data) + appkey).encode("utf-8")).hexdigest()


def _verify_sign(data: dict, appkey: str, sign: str) -> bool:
    return _make_sign(data, appkey) == sign


class EpayPayment(BasePayment):
    """易支付上游通道。"""

    meta = PluginMeta(
        name="epay",
        showname="易支付上游通道",
        types=["alipay", "wxpay", "qqpay", "bank"],
        inputs=[
            {"name": "appurl", "title": "接口地址", "type": "input",
             "placeholder": "https://pay.example.com/（上游站点根地址，以 / 结尾）", "required": True},
            {"name": "appid", "title": "商户ID（pid）", "type": "input",
             "placeholder": "如 1001（上游商户后台的 pid，纯数字）", "required": True},
            {"name": "appkey", "title": "商户密钥", "type": "input",
             "placeholder": "上游商户后台分配的密钥，用于 MD5 签名", "required": True},
            {"name": "appswitch", "title": "是否使用mapi接口", "type": "select",
             "options": {"0": "否", "1": "是"}},
        ],
    )

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True

    # ------------------------------------------------------------

    def _api_url(self, path: str) -> str:
        base = str(self.channel.get("appurl", "") or "").strip()
        if not base:
            raise ValueError("通道未配置接口地址(appurl)")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        return base.rstrip("/") + "/" + path

    def _base_params(self, ctx: PaymentContext) -> dict:
        return {
            "pid": str(self.channel.get("appid", "") or "").strip(),
            "out_trade_no": ctx.order["trade_no"],
            "notify_url": ctx.notify_url,
            "return_url": ctx.return_url,
            "name": ctx.order["subject"],
            "money": ctx.order["realmoney"],
        }

    @staticmethod
    def _device(is_mobile: bool) -> str:
        return "mobile" if is_mobile else "pc"

    # ------------------------------------------------------------

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        appswitch = str(self.channel.get("appswitch", "0") or "0")
        if appswitch == "1":
            return await self._submit_mapi(ctx)
        return await self._submit_jump(ctx)

    async def _submit_jump(self, ctx: PaymentContext) -> PaymentResult:
        params = self._base_params(ctx)
        params["type"] = ctx.order["payment_type_code"]
        params["sign"] = _make_sign(params, str(self.channel.get("appkey", "")))
        params["sign_type"] = "MD5"
        url = self._api_url("submit.php") + "?" + urlencode(params)
        return PaymentResult(kind="jump", url=url)

    async def _submit_mapi(self, ctx: PaymentContext) -> PaymentResult:
        params = self._base_params(ctx)
        params["type"] = ctx.order["payment_type_code"]
        params["device"] = self._device(ctx.is_mobile)
        if ctx.client_ip:
            params["clientip"] = ctx.client_ip
        params["sign"] = _make_sign(params, str(self.channel.get("appkey", "")))
        params["sign_type"] = "MD5"

        client = get_http_client()
        try:
            resp = await client.post(self._api_url("mapi.php"), data=params)
        except Exception as e:
            _log.warning("epay mapi 请求失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        result = {}
        try:
            result = resp.json()
        except Exception:
            return PaymentResult(kind="error", msg="上游返回数据解析失败")

        if result.get("code") == 1:
            if result.get("payurl"):
                return PaymentResult(kind="jump", url=str(result["payurl"]))
            if result.get("qrcode"):
                return PaymentResult(kind="qrcode", url=str(result["qrcode"]))
            if result.get("urlscheme"):
                return PaymentResult(kind="scheme", url=str(result["urlscheme"]))
            return PaymentResult(kind="error", msg="未返回支付链接")
        return PaymentResult(kind="error", msg=str(result.get("msg") or "上游下单失败"))

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        """act=order 查单。上游 status: 0=未支付 1=已支付。"""
        params = {
            "act": "order",
            "pid": str(self.channel.get("appid", "") or "").strip(),
            "key": str(self.channel.get("appkey", "") or ""),
        }
        if ctx.order.get("api_trade_no"):
            params["trade_no"] = ctx.order["api_trade_no"]
        else:
            params["out_trade_no"] = ctx.order["trade_no"]

        client = get_http_client()
        try:
            resp = await client.get(self._api_url("api.php"), params=params)
            data = resp.json()
        except Exception as e:
            _log.warning("epay 查单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return QueryResult(status="unknown")

        if data.get("code") != 1:
            return QueryResult(status="unknown", raw=data)

        status = "unpaid"
        if str(data.get("status")) == "1":
            status = "paid"
        money = None
        try:
            money = Decimal(str(data.get("money")))
        except (InvalidOperation, TypeError, ValueError):
            pass
        return QueryResult(
            status=status,
            api_trade_no=str(data.get("trade_no") or ""),
            money=money,
            buyer_id=str(data.get("buyer") or ""),
            raw=data,
        )

    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        sign = str(ctx_params.get("sign", ""))
        if not sign or not _verify_sign(ctx_params, str(self.channel.get("appkey", "")), sign):
            return NotifyVerdict(ok=False, ack="fail")

        if str(ctx_params.get("trade_status", "")) != "TRADE_SUCCESS":
            # 验签通过但非成功状态：应答 success 让上游停止重发
            return NotifyVerdict(ok=False, ack="success")

        money = None
        try:
            money = Decimal(str(ctx_params.get("money")))
        except (InvalidOperation, TypeError, ValueError):
            return NotifyVerdict(ok=False, ack="fail")

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(ctx_params.get("trade_no") or ""),
            money=money,
            buyer_id=str(ctx_params.get("buyer") or ""),
            ack="success",
        )

    async def return_handler(self, ctx_params: dict) -> dict | None:
        sign = str(ctx_params.get("sign", ""))
        if not sign or not _verify_sign(ctx_params, str(self.channel.get("appkey", "")), sign):
            return None
        if str(ctx_params.get("trade_status", "")) != "TRADE_SUCCESS":
            return None
        return {"status": "paid", "api_trade_no": str(ctx_params.get("trade_no") or "")}

    # ------------------------------------------------------------

    async def refund(self, order: dict, refund_no: str, amount: Decimal) -> dict:
        params = {
            "act": "refund",
            "pid": str(self.channel.get("appid", "") or "").strip(),
            "key": str(self.channel.get("appkey", "") or ""),
            "money": str(amount),
            "trade_no": order.get("api_trade_no") or "",
            "out_trade_no": refund_no,
        }
        if not params["trade_no"]:
            return {"status": 0, "msg": "缺少上游订单号，无法退款"}

        client = get_http_client()
        try:
            resp = await client.get(self._api_url("api.php"), params=params)
            data = resp.json()
        except Exception as e:
            _log.warning("epay 退款请求失败 refund_no=%s: %s", refund_no, e)
            return {"status": 0, "msg": "上游通道连接失败"}

        if data.get("code") == 0:
            return {"status": 1, "msg": str(data.get("msg") or "退款成功")}
        return {"status": 0, "msg": str(data.get("msg") or "退款失败")}


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = EpayPayment
