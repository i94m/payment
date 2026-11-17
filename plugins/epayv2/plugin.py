"""易支付 V2 上游通道（彩虹易支付 V2 协议）插件。

与 plugins/epay（易支付 V1，MD5 签名）是**两套不同协议**，不能共用一个插件：
- V1：submit.php / mapi.php / api.php，MD5 拼串签名，返回 code=1 成功
- V2：api/pay/submit、api/pay/create、api/pay/query、api/pay/refund，
      **RSA2（SHA256WithRSA/PKCS1v15）** 签名，返回 code=0 成功，
      pay_type/pay_info 描述支付动作，回调/响应带 timestamp 且需在 300s 内

对接上游网关接口：
- appswitch=0：跳转 {appurl}api/pay/submit?签名参数（表单 POST 亦可）
- appswitch=1：POST {appurl}api/pay/create 下单，按 pay_type 取 pay_info
- 回调：GET 带签名参数（sign + timestamp），平台公钥验签 + TRADE_SUCCESS 判成功
- 查单：POST {appurl}api/pay/query（trade_no）
- 退款：POST {appurl}api/pay/refund（trade_no + money + out_refund_no）

凭证：appid=商户ID(pid)、appkey=平台公钥（验签）、appsecret=商户私钥（签名）。
金额校验不在此处做（services/order.process_paid_order 统一比对 realmoney）。
"""
import logging
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from app.lib.sign import make_rsa_sign, verify_rsa_sign
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

# 上游要求 timestamp 与服务器时间相差不超过 300 秒（响应与回调都要校验）
_TIMESTAMP_WINDOW = 300


class EpayV2Payment(BasePayment):
    """易支付 V2 上游通道（RSA2 签名）。"""

    meta = PluginMeta(
        name="epayv2",
        showname="易支付V2",
        types=["alipay", "wxpay", "qqpay", "bank"],
        inputs=[
            {"name": "appurl", "title": "接口地址", "type": "input",
             "placeholder": "https://pay.example.com/（上游站点根地址，以 / 结尾）", "required": True},
            {"name": "appid", "title": "商户ID（pid）", "type": "input",
             "placeholder": "如 1001（上游商户后台的 pid，纯数字）", "required": True},
            {"name": "appkey", "title": "平台公钥", "type": "input",
             "placeholder": "上游商户后台提供的平台公钥（单行 base64，可含 -----BEGIN PUBLIC KEY----- 头尾）", "required": True},
            {"name": "appsecret", "title": "商户私钥", "type": "input",
             "placeholder": "上游商户后台生成的商户私钥（单行 base64，可含 -----BEGIN PRIVATE KEY----- 头尾），用于 RSA2 签名", "required": True},
            {"name": "appswitch", "title": "是否使用mapi接口", "type": "select",
             "options": {"0": "否", "1": "是"}},
        ],
        version="1.0.0",
    )

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True

    # ------------------------------------------------------------
    # 凭证 / 地址
    # ------------------------------------------------------------

    def _api_url(self, path: str) -> str:
        base = str(self.channel.get("appurl", "") or "").strip()
        if not base:
            raise ValueError("通道未配置接口地址(appurl)")
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        return base.rstrip("/") + "/" + path.lstrip("/")

    def _pid(self) -> str:
        return str(self.channel.get("appid", "") or "").strip()

    def _public_key(self) -> str:
        return str(self.channel.get("appkey", "") or "").strip()

    def _private_key(self) -> str:
        return str(self.channel.get("appsecret", "") or "").strip()

    # ------------------------------------------------------------
    # 签名 / 验签
    # ------------------------------------------------------------

    def _signed(self, payload: dict) -> dict:
        """补 pid/timestamp 并 RSA2 签名（签名原文排除 sign/sign_type 与空值）。"""
        private_key = self._private_key()
        if not private_key:
            raise ValueError("通道未配置商户私钥(appsecret)")
        params = dict(payload)
        params["pid"] = self._pid()
        params["timestamp"] = str(int(time.time()))
        params["sign"] = make_rsa_sign(params, private_key)
        params["sign_type"] = "RSA"
        return params

    def _verify(self, data: dict) -> bool:
        """平台公钥验签：sign 必填、timestamp 需在 300s 窗口内。"""
        if not isinstance(data, dict):
            return False
        sign = str(data.get("sign") or "")
        public_key = self._public_key()
        if not sign or not public_key:
            return False
        try:
            ts = int(str(data.get("timestamp") or "0"))
        except ValueError:
            return False
        if not ts or abs(int(time.time()) - ts) > _TIMESTAMP_WINDOW:
            return False
        return verify_rsa_sign(data, public_key, sign)

    # ------------------------------------------------------------
    # 下单
    # ------------------------------------------------------------

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        appswitch = str(self.channel.get("appswitch", "0") or "0")
        if appswitch == "1":
            return await self._submit_api(ctx)
        return self._submit_jump(ctx)

    def _order_params(self, ctx: PaymentContext) -> dict:
        return {
            "type": ctx.order["payment_type_code"],
            "out_trade_no": ctx.order["trade_no"],
            "notify_url": ctx.notify_url,
            "return_url": ctx.return_url,
            "name": ctx.order["subject"],
            "money": ctx.order["realmoney"],
        }

    def _submit_jump(self, ctx: PaymentContext) -> PaymentResult:
        try:
            params = self._signed(self._order_params(ctx))
            url = self._api_url("api/pay/submit") + "?" + urlencode(params)
        except Exception as e:
            _log.warning("epayv2 下单参数生成失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="通道凭证配置错误")
        return PaymentResult(kind="jump", url=url)

    async def _submit_api(self, ctx: PaymentContext) -> PaymentResult:
        payload = self._order_params(ctx)
        payload["method"] = "web"
        payload["device"] = "mobile" if ctx.is_mobile else "pc"
        if ctx.client_ip:
            payload["clientip"] = ctx.client_ip
        try:
            params = self._signed(payload)
        except Exception as e:
            _log.warning("epayv2 下单参数生成失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="通道凭证配置错误")

        client = get_http_client()
        try:
            resp = await client.post(self._api_url("api/pay/create"), data=params)
        except Exception as e:
            _log.warning("epayv2 下单请求失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        try:
            data = resp.json()
        except Exception:
            return PaymentResult(kind="error", msg="上游返回数据解析失败")
        if not isinstance(data, dict):
            return PaymentResult(kind="error", msg="上游返回数据解析失败")

        if str(data.get("code")) != "0":
            return PaymentResult(kind="error", msg=str(data.get("msg") or "上游下单失败"))
        if not self._verify(data):
            _log.warning("epayv2 下单返回验签失败 trade_no=%s", ctx.order["trade_no"])
            return PaymentResult(kind="error", msg="上游返回验签失败")

        pay_type = str(data.get("pay_type") or "").lower()
        pay_info = str(data.get("pay_info") or "")
        if not pay_info:
            return PaymentResult(kind="error", msg="未返回支付信息")
        if pay_type == "jump":
            return PaymentResult(kind="jump", url=pay_info)
        if pay_type == "html":
            return PaymentResult(kind="html", html=pay_info)
        if pay_type == "urlscheme":
            return PaymentResult(kind="scheme", url=pay_info)
        # 其余（qrcode / wap 等）统一按二维码内容返回
        return PaymentResult(kind="qrcode", url=pay_info)

    # ------------------------------------------------------------
    # 主动查单：api/pay/query，status: 0=未支付 1=已支付
    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        payload = {}
        if ctx.order.get("api_trade_no"):
            payload["trade_no"] = ctx.order["api_trade_no"]
        else:
            payload["out_trade_no"] = ctx.order["trade_no"]
        try:
            params = self._signed(payload)
        except Exception as e:
            _log.warning("epayv2 查单参数生成失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return QueryResult(status="unknown")

        client = get_http_client()
        try:
            resp = await client.post(self._api_url("api/pay/query"), data=params)
            data = resp.json()
        except Exception as e:
            _log.warning("epayv2 查单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return QueryResult(status="unknown")

        if not isinstance(data, dict) or str(data.get("code")) != "0":
            return QueryResult(status="unknown", raw=data if isinstance(data, dict) else {})
        if not self._verify(data):
            _log.warning("epayv2 查单返回验签失败 trade_no=%s", ctx.order["trade_no"])
            return QueryResult(status="unknown")

        status = "paid" if str(data.get("status")) == "1" else "unpaid"
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
    # 回调 / 回跳
    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        if not self._verify(ctx_params):
            return NotifyVerdict(ok=False, ack="fail")

        if str(ctx_params.get("trade_status", "")) != "TRADE_SUCCESS":
            # 验签通过但非成功状态：应答 success 让上游停止重发
            return NotifyVerdict(ok=False, ack="success")

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
        if not self._verify(ctx_params):
            return None
        if str(ctx_params.get("trade_status", "")) != "TRADE_SUCCESS":
            return None
        return {"status": "paid", "api_trade_no": str(ctx_params.get("trade_no") or "")}

    # ------------------------------------------------------------
    # 退款：api/pay/refund，code=0 成功
    # ------------------------------------------------------------

    async def refund(self, order: dict, refund_no: str, amount: Decimal) -> dict:
        trade_no = order.get("api_trade_no") or ""
        if not trade_no:
            return {"status": 0, "msg": "缺少上游订单号，无法退款"}
        payload = {
            "trade_no": trade_no,
            "money": str(amount),
            "out_refund_no": refund_no,
        }
        try:
            params = self._signed(payload)
        except Exception as e:
            _log.warning("epayv2 退款参数生成失败 refund_no=%s: %s", refund_no, e)
            return {"status": 0, "msg": "通道凭证配置错误"}

        client = get_http_client()
        try:
            resp = await client.post(self._api_url("api/pay/refund"), data=params)
            data = resp.json()
        except Exception as e:
            _log.warning("epayv2 退款请求失败 refund_no=%s: %s", refund_no, e)
            return {"status": 0, "msg": "上游通道连接失败"}

        if not isinstance(data, dict):
            return {"status": 0, "msg": "上游返回数据解析失败"}
        if str(data.get("code")) != "0":
            return {"status": 0, "msg": str(data.get("msg") or "退款失败")}
        if not self._verify(data):
            _log.warning("epayv2 退款返回验签失败 refund_no=%s", refund_no)
            return {"status": 0, "msg": "上游返回验签失败"}
        return {"status": 1, "msg": str(data.get("msg") or "退款成功")}


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = EpayV2Payment
