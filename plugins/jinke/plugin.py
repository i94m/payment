"""金科支付（jinke）插件 — MD5 签名 + JSON 下单 + JSON 回调。

移植自老 epay 项目 `plugins/jinke/jinke_plugin.php`，协议要点：

- 下单：POST {appurl}，`Content-Type: application/json`
  字段：mchId（商户号）/ productId（通道编码）/ outTradeNo / amount（分，整数）/
  reqTime（毫秒时间戳）/ notifyUrl / returnUrl / sign
- 响应：`code == "0"` 且 `data.payUrl` 为支付跳转地址
- 回调：JSON POST，验签后 `state == "1"` 为支付成功，金额单位分（amount / 100），
  应答 `OK` / `FAIL`
- 同步回跳：GET 携带同样的签名参数，`state == "1"` 视为已支付

签名规则：参数按 key ASCII 升序，按 `k=v&` 逐项拼接（跳过 sign 与空值，`"0"` 参与），
末尾拼 `key=密钥`，MD5 小写。

金额业务比对由 services/order.process_paid_order 统一执行，插件只报数。
"""
import hashlib
import logging
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

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


def _make_sign(params: dict, key: str) -> str:
    """签名：key 升序 `k=v&` 拼接 + `key=密钥`，MD5 小写。"""
    keys = sorted(
        k for k, v in params.items()
        if k != "sign"
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    content = "&".join(f"{k}={params[k]}" for k in keys) + "&key=" + key
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _money_to_cents(value) -> int:
    """元 → 分（整数）。"""
    return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _cents_to_money(value) -> Decimal | None:
    """分 → 元；解析失败返回 None（由调用方显式拒绝，不放行）。"""
    try:
        return (Decimal(str(value)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


class JinkePayment(BasePayment):
    """金科支付（MD5 签名，JSON 协议）。"""

    # 上游要求的成功应答是 "OK"（不是 success）
    notify_ok_ack = "OK"

    meta = PluginMeta(
        name="jinke",
        showname="金科支付",
        types=["alipay", "wxpay"],
        inputs=[
            {"name": "appurl", "title": "网关地址", "type": "input",
             "placeholder": "下单接口完整地址", "required": True},
            {"name": "appid", "title": "商户号", "type": "input",
             "placeholder": "商户号", "required": True},
            {"name": "appkey", "title": "商户密钥", "type": "input",
             "placeholder": "32位MD5密钥", "required": True},
            {"name": "productid", "title": "通道编码", "type": "input",
             "placeholder": "通道编码", "required": True},
        ],
        version="1.0.0",
    )

    # ------------------------------------------------------------

    def _cfg(self, name: str) -> str:
        return str(self.channel.get(name, "") or "").strip()

    def _api_url(self) -> str:
        url = self._cfg("appurl")
        if not url:
            raise ValueError("通道未配置网关地址(appurl)")
        return url

    # ------------------------------------------------------------

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        params = {
            "mchId": self._cfg("appid"),
            "productId": self._cfg("productid"),
            "outTradeNo": ctx.order["trade_no"],
            "amount": _money_to_cents(ctx.order["realmoney"]),
            "reqTime": int(time.time() * 1000),
            "notifyUrl": ctx.notify_url,
            "returnUrl": ctx.return_url,
        }
        params["sign"] = _make_sign(params, self._cfg("appkey"))

        try:
            resp = await get_http_client().post(self._api_url(), json=params)
            body = resp.json()
        except Exception as e:
            _log.warning("jinke 下单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        if not isinstance(body, dict):
            return PaymentResult(kind="error", msg="上游返回数据解析失败")

        if str(body.get("code")) != "0":
            return PaymentResult(kind="error", msg=str(body.get("msg") or "上游下单失败"))

        data = body.get("data")
        if isinstance(data, dict) and data.get("payUrl"):
            return PaymentResult(kind="jump", url=str(data["payUrl"]))
        if isinstance(data, str) and data.startswith(("http://", "https://")):
            return PaymentResult(kind="jump", url=data)
        return PaymentResult(kind="error", msg="未返回支付链接")

    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        sign = str(ctx_params.get("sign", "") or "")
        if not sign or _make_sign(ctx_params, self._cfg("appkey")).lower() != sign.lower():
            return NotifyVerdict(ok=False, ack="FAIL")

        if str(ctx_params.get("state", "")) != "1":
            # 验签通过但非成功状态：应答 OK 让上游停止重发
            return NotifyVerdict(ok=False, ack="OK")

        money = _cents_to_money(ctx_params.get("amount"))
        if money is None:
            return NotifyVerdict(ok=False, ack="FAIL")

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(ctx_params.get("tradeNo") or ""),
            money=money,
            ack="OK",
        )

    async def return_handler(self, ctx_params: dict) -> dict | None:
        sign = str(ctx_params.get("sign", "") or "")
        if not sign or _make_sign(ctx_params, self._cfg("appkey")).lower() != sign.lower():
            return None
        if str(ctx_params.get("state", "")) != "1":
            return None
        money = _cents_to_money(ctx_params.get("amount"))
        if money is None:
            return None
        return {
            "status": "paid",
            "api_trade_no": str(ctx_params.get("tradeNo") or ""),
            "out_trade_no": str(ctx_params.get("outTradeNo") or ""),
            "money": str(money),
        }

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        """未对接查单接口（老插件同样没有），一律 unknown 交给人工/回调补单。"""
        return QueryResult(status="unknown")


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = JinkePayment
