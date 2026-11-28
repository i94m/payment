"""RS支付（rspay）插件 — MD5（大写）签名 + 表单下单 + 表单回调。

移植自老 epay 项目 `plugins/rspay/rspay_plugin.php`，协议要点：

- 下单：POST {appurl}，`application/x-www-form-urlencoded`
  字段：pay_memberid（商户号）/ pay_orderid / pay_applydate（`Y-m-d H:i:s`）/
  pay_bankcode（通道编码）/ pay_amount（元）/ pay_notifyurl / pay_callbackurl /
  pay_productname / pay_md5sign
- 响应：`status == "1"` 且 `h5_url` 为支付跳转地址
- 回调：form-urlencoded，签名只覆盖 memberid / orderid / amount / datetime /
  transaction_id / returncode 六个字段（其余字段不参与），`returncode == "00"` 为支付成功，
  金额单位元，应答 `OK` / `FAIL`
- 同步回跳：GET 携带同样六个字段 + sign

签名规则：参与签名的字段按 key ASCII 升序，按 `k=v&` 逐项拼接（跳过空值，`"0"` 参与），
末尾拼 `key=密钥`，MD5 **大写**。

金额业务比对由 services/order.process_paid_order 统一执行，插件只报数。
"""
import hashlib
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

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

# 回调签名只覆盖这六个字段（上游文档口径），额外字段不参与
_NOTIFY_FIELDS = ("memberid", "orderid", "amount", "datetime", "transaction_id", "returncode")


def _make_sign(params: dict, key: str) -> str:
    """签名：key 升序 `k=v&` 拼接 + `key=密钥`，MD5 大写。"""
    keys = sorted(
        k for k, v in params.items()
        if k not in ("sign", "pay_md5sign")
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    content = "&".join(f"{k}={params[k]}" for k in keys) + "&key=" + key
    return hashlib.md5(content.encode("utf-8")).hexdigest().upper()


def _to_money(value) -> Decimal | None:
    """回调金额（元）解析；失败返回 None，由调用方显式拒绝。"""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class RsPayPayment(BasePayment):
    """RS支付（MD5 大写签名，form 协议）。"""

    # 上游要求的成功应答是 "OK"（不是 success）
    notify_ok_ack = "OK"

    meta = PluginMeta(
        name="rspay",
        showname="RS支付",
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
            "pay_memberid": self._cfg("appid"),
            "pay_orderid": ctx.order["trade_no"],
            "pay_applydate": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pay_bankcode": self._cfg("productid"),
            "pay_amount": f"{Decimal(str(ctx.order['realmoney'])):.2f}",
            "pay_notifyurl": ctx.notify_url,
            "pay_callbackurl": ctx.return_url,
            "pay_productname": str(ctx.order.get("subject") or ""),
        }
        params["pay_md5sign"] = _make_sign(params, self._cfg("appkey"))

        try:
            resp = await get_http_client().post(self._api_url(), data=params)
            body = resp.json()
        except Exception as e:
            _log.warning("rspay 下单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        if not isinstance(body, dict):
            return PaymentResult(kind="error", msg="上游返回数据解析失败")

        if str(body.get("status")) == "1" and body.get("h5_url"):
            return PaymentResult(kind="jump", url=str(body["h5_url"]))
        return PaymentResult(kind="error", msg=str(body.get("msg") or "上游下单失败"))

    # ------------------------------------------------------------

    @staticmethod
    def _sign_fields(ctx_params: dict) -> dict:
        """回调签名只取固定的六个字段（上游口径，多传字段不参与）。"""
        return {k: str(ctx_params.get(k, "") or "") for k in _NOTIFY_FIELDS}

    def _verify(self, ctx_params: dict) -> bool:
        sign = str(ctx_params.get("sign", "") or "").strip()
        if not sign:
            return False
        expected = _make_sign(self._sign_fields(ctx_params), self._cfg("appkey"))
        return expected == sign.upper()

    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        if not self._verify(ctx_params):
            return NotifyVerdict(ok=False, ack="FAIL")

        if str(ctx_params.get("returncode", "")) != "00":
            # 验签通过但非成功状态：应答 OK 让上游停止重发
            return NotifyVerdict(ok=False, ack="OK")

        money = _to_money(ctx_params.get("amount"))
        if money is None:
            return NotifyVerdict(ok=False, ack="FAIL")

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(ctx_params.get("transaction_id") or ""),
            money=money,
            ack="OK",
        )

    async def return_handler(self, ctx_params: dict) -> dict | None:
        if not self._verify(ctx_params):
            return None
        if str(ctx_params.get("returncode", "")) != "00":
            return None
        money = _to_money(ctx_params.get("amount"))
        if money is None:
            return None
        return {
            "status": "paid",
            "api_trade_no": str(ctx_params.get("transaction_id") or ""),
            "out_trade_no": str(ctx_params.get("orderid") or ""),
            "money": str(money),
        }

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        """未对接查单接口（老插件同样没有），一律 unknown 交给人工/回调补单。"""
        return QueryResult(status="unknown")


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = RsPayPayment
