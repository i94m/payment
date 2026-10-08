"""双子支付上游插件。

协议要点：
- 下单：POST form 到 {appurl}（默认 http://137.220.128.173/gateway/pay/api），
  响应 code==200 且 data.payUrl 存在 → jump 跳转支付
- 查单：POST form 到 {appurl 去掉末段}/query，data.orderStatus==2 为已支付
- 回调：GET / POST-form / POST-body 均兼容，验签 + orderStatus==2 判成功，
  应答纯文本 success/fail（忽略大小写；上游失败后 5s/10s/20s/40s 重试共 5 次）
- 签名：排除 sign 与空值（"0" 保留），键 ASCII 升序 "k=v" 用 & 连接，
  末尾拼 "&key=商户密钥"，MD5 小写（文档示例向量已离线验证通过）
- type 是数字支付方式编码（通道配置 productid，如 123），不是 alipay/wxpay 字样
- 金额必须两位小数（sprintf %.2f 语义，ROUND_HALF_UP）
- 不支持退款（协议无退款接口）；上游的网关/商户号锁定属部署期防篡改措施，
  本系统通道凭证由 admin 存库管理，不再硬编码

金额校验不在此处做（services/order.process_paid_order 统一比对 realmoney）。
"""
import hashlib
import logging
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

DEFAULT_GATEWAY = "http://137.220.128.173/gateway/pay/api"

# 支付成功状态码（双子 orderStatus）
STATUS_PAID = "2"


def _sign_content(data: dict) -> str:
    """双子签名原文：排除 sign 与空值（"0" 保留），键 ASCII 升序 "k=v" & 连接。"""
    keys = sorted(
        k for k, v in data.items()
        if str(k).lower() != "sign"
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    return "&".join(f"{k}={data[k]}" for k in keys)


def make_sign(data: dict, key: str) -> str:
    content = _sign_content(data) + "&key=" + key
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _verify_sign(data: dict, key: str, sign: str) -> bool:
    # PHP 侧用 strcasecmp 比对，这里等价忽略大小写
    return make_sign(data, key).lower() == str(sign).lower()


def _money_2dp(value) -> str:
    """金额两位小数（sprintf %.2f：四舍五入）。"""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_money(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class ShuangziPayment(BasePayment):
    """双子支付协议上游。"""

    meta = PluginMeta(
        name="shuangzi",
        showname="双子支付",
        types=["alipay", "wxpay"],
        inputs=[
            {"name": "appurl", "title": "下单网关地址", "type": "input",
             "placeholder": DEFAULT_GATEWAY, "required": True},
            {"name": "appmchid", "title": "商户号（merchantId）", "type": "input",
             "placeholder": "1127", "required": True},
            {"name": "appkey", "title": "商户密钥", "type": "input",
             "placeholder": "双子商户后台分配的密钥，用于 MD5 签名", "required": True},
            {"name": "productid", "title": "支付方式编码（type，数字）", "type": "input",
             "placeholder": "如 123，不同支付方式编码不同，需向平台确认", "required": True},
        ],
    )

    # ------------------------------------------------------------

    def _cfg(self, name: str, default: str = "") -> str:
        return str(self.channel.get(name, "") or "").strip() or default

    def _gateway(self) -> str:
        url = self._cfg("appurl", DEFAULT_GATEWAY)
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        return url.rstrip("/")

    def _query_url(self) -> str:
        # 下单网关 .../pay/api → 查询网关 .../pay/query
        head, _, tail = self._gateway().rpartition("/")
        if tail == "api":
            return head + "/query"
        return self._gateway() + "/query"

    async def _post_form(self, url: str, data: dict) -> dict:
        """POST form-urlencoded，返回解析后的 JSON dict；失败抛异常。"""
        client = get_http_client()
        resp = await client.post(url, data=data)
        return resp.json()

    # ------------------------------------------------------------

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        params = {
            "merchantId": self._cfg("appmchid"),
            "type": self._cfg("productid"),  # 数字支付方式编码
            "orderId": ctx.order["trade_no"],
            "amount": _money_2dp(ctx.order["realmoney"]),
            "notifyUrl": ctx.notify_url,
            "returnUrl": ctx.return_url,
            "failUrl": ctx.return_url,
            "ip": ctx.client_ip or "127.0.0.1",
            "attach": ctx.order["trade_no"],
        }
        params["sign"] = make_sign(params, self._cfg("appkey"))

        try:
            body = await self._post_form(self._gateway(), params)
        except Exception as e:
            _log.warning("shuangzi 下单请求失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        if not isinstance(body, dict):
            return PaymentResult(kind="error", msg="上游返回数据解析失败")

        if str(body.get("code")) == "200" and body.get("data", {}).get("payUrl"):
            return PaymentResult(kind="jump", url=str(body["data"]["payUrl"]))

        msg = str(body.get("msg") or "未知错误")
        _log.info("shuangzi 下单失败 trade_no=%s code=%s msg=%s",
                  ctx.order["trade_no"], body.get("code"), msg)
        return PaymentResult(kind="error", msg="创建订单失败！" + msg)

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        params = {
            "merchantId": self._cfg("appmchid"),
            "orderId": ctx.order["trade_no"],
        }
        params["sign"] = make_sign(params, self._cfg("appkey"))

        try:
            data = await self._post_form(self._query_url(), params)
        except Exception as e:
            _log.warning("shuangzi 查单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return QueryResult(status="unknown")

        payload = data.get("data") if isinstance(data, dict) else None
        if str(data.get("code")) != "200" or not isinstance(payload, dict):
            return QueryResult(status="unknown", raw=data)

        status = "paid" if str(payload.get("orderStatus")) == STATUS_PAID else "unpaid"
        return QueryResult(
            status=status,
            api_trade_no=str(payload.get("orderNo") or ""),
            money=_parse_money(payload.get("amount")),
            raw=data,
        )

    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        sign = str(ctx_params.get("sign", ""))
        if not sign or not _verify_sign(ctx_params, self._cfg("appkey"), sign):
            return NotifyVerdict(ok=False, ack="fail")

        if str(ctx_params.get("orderStatus", "")) != STATUS_PAID:
            # 验签通过但非成功状态：应答 success 让上游停止重发
            return NotifyVerdict(ok=False, ack="success")

        money = _parse_money(ctx_params.get("amount"))
        if money is None:
            return NotifyVerdict(ok=False, ack="fail")

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(ctx_params.get("orderNo") or ""),
            money=money,
            ack="success",
        )


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = ShuangziPayment
