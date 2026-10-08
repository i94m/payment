"""仙女支付2上游插件（乐事 leshiapi 协议）。

协议要点（对照网关实测校准）：

- 下单：POST JSON 到 {appurl}（默认 http://lantu-f78pis9s8s5qyk.coinsageai.vip/api/merchant/getOrder）
  参数 merchantId/bizOrderNo/money(元两位小数)/payType/bizNotifyUrl/sign
- 查单：POST JSON 到 {appurl 同域}/api/merchant/query，参数 merchantId/bizOrderNo/sign
- 签名：排除 sign 与空值（"0" 保留），键 ASCII 升序 "k=v" 用 & 连接，
  末尾拼 "&secret=商户密钥"，MD5 小写（网关回显 sign_str 已实测比对通过）
- 验签兜底：secret 作为普通参数一起参与升序排序（不追加末尾）的变体
- 商户订单号长度必须 >= 10（过短返回「金额不支持或订单号长度低于10」）
- 响应：失败 {"code":-1,"message":"..."}；成功 code∈{0,1,200,success,SUCCESS}，
  支付链接按常见命名候选匹配（payUrl/url/codeUrl/…）
- 回调：JSON POST（GET/form 兼容），验签 + 状态字段白名单判成功，
  应答纯文本 success/fail
- 不支持退款（协议无退款接口）

金额校验不在此处做（services/order.process_paid_order 统一比对 realmoney）。
上游在 Cloudflare 后，必须带浏览器 UA，否则 403。
"""
import hashlib
import hmac
import logging
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.services import http_client
from app.services.plugin_interfaces import (
    BasePayment,
    NotifyVerdict,
    PaymentContext,
    PaymentResult,
    PluginMeta,
    QueryResult,
)

_log = logging.getLogger(__name__)

DEFAULT_GATEWAY = "http://lantu-f78pis9s8s5qyk.coinsageai.vip/api/merchant/getOrder"

# 网关 Cloudflare 拦截非浏览器 UA（403 / error code 1010），必须伪装浏览器
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 下单成功的 code 白名单（上游兼容多套，按 PHP 版逻辑保留）
OK_CODES = {"0", "1", "200", "success", "SUCCESS"}

# 支付成功状态值白名单（回调状态字段）
PAID_VALUES = {"1", "2", "success", "SUCCESS", "paid", "PAID",
               "TRADE_SUCCESS", "PAY_SUCCESS", "pay_success",
               "true", "ok", "OK"}

# 回调状态字段候选（按顺序取第一个非空）
STATUS_FIELDS = [
    "status", "orderStatus", "order_status", "tradeStatus", "trade_status",
    "payStatus", "pay_status", "state", "tradeState", "result", "orderState",
]

# 商户订单号字段候选
BIZ_ORDER_FIELDS = ["bizOrderNo", "merchantOrderNo", "merchantOrderId",
                    "outTradeNo", "biz_no", "mchOrderNo"]

# 平台单号字段候选
API_ORDER_FIELDS = ["orderNo", "tradeNo", "sysOrderNo", "platformOrderNo",
                    "transactionId", "sys_order_id"]

# 金额字段候选
MONEY_FIELDS = ["money", "amount", "payMoney", "payAmount",
                "orderMoney", "realMoney"]

# 支付链接字段候选（http 开头）
URL_FIELDS = ["payUrl", "pay_url", "url", "codeUrl", "code_url",
              "payLink", "pay_link", "jumpUrl", "jump_url",
              "h5Url", "h5_url", "cashierUrl", "cashier_url",
              "orderUrl", "order_url", "payment_url", "paymentUrl"]

# 二维码内容字段候选
QR_FIELDS = ["qrCode", "qr_code", "qrContent", "qr_content",
             "payCode", "pay_code"]


def _sign_content(data: dict) -> str:
    """签名原文：排除 sign 与空值（"0" 保留），键 ASCII 升序 "k=v" & 连接。"""
    keys = sorted(
        k for k, v in data.items()
        if k != "sign"
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    return "&".join(f"{k}={data[k]}" for k in keys)


def make_sign(data: dict, key: str) -> str:
    """主签名：升序 k=v& + "&secret=" + 密钥 → md5 小写。"""
    content = _sign_content(data) + "&secret=" + key
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _sign_fallback(data: dict, key: str) -> str:
    """兜底签名：secret 作为普通参数参与升序排序（不追加末尾）。"""
    merged = {k: v for k, v in data.items()
              if k != "sign" and v is not None and v != ""
              and not isinstance(v, (list, dict))}
    merged["secret"] = key
    content = "&".join(f"{k}={merged[k]}" for k in sorted(merged))
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _verify_sign(data: dict, key: str, sign: str) -> bool:
    """上游签名比对大小写不敏感（对齐 PHP strcasecmp）；主算法 + 兜底。"""
    got = str(sign).strip().lower()
    return (hmac.compare_digest(make_sign(data, key), got)
            or hmac.compare_digest(_sign_fallback(data, key), got))


def _money_2dp(value) -> str:
    """金额两位小数（sprintf %.2f 语义：四舍五入）。"""
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_money(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _pick(data: dict, fields: list[str]) -> str:
    """按候选字段名取值（第一个非空非容器的字符串值）。"""
    for f in fields:
        v = data.get(f)
        if v is not None and v != "" and not isinstance(v, (list, dict)):
            return str(v)
    return ""


def _is_paid(data: dict) -> bool:
    """支付成功判定：状态字段存在按白名单；缺失时按成功处理（同族接口多数只在成功时推送）。"""
    for f in STATUS_FIELDS:
        v = data.get(f)
        if v is not None and v != "":
            if isinstance(v, bool):
                s = "1" if v else "0"
            else:
                s = str(v)
            return s in PAID_VALUES
    # 无状态字段：放行（PHP 版同逻辑）
    return True


def _parse_pay_result(data: dict) -> PaymentResult:
    """解析下单响应（独立方法便于离线测试各分支）。"""
    code = str(data.get("code", "") or "")
    ok_code = code in OK_CODES

    body = data.get("data") if isinstance(data.get("data"), dict) else data

    # 1. 明确的支付链接字段
    for f in URL_FIELDS:
        v = body.get(f)
        if v and isinstance(v, str) and v.strip().lower().startswith(("http://", "https://")):
            return PaymentResult(kind="jump", url=v.strip())

    # 2. 二维码内容字段（非 http 链接时按扫码页渲染）
    for f in QR_FIELDS:
        v = body.get(f)
        if v and isinstance(v, str):
            payload = v.strip()
            if payload.lower().startswith(("http://", "https://")):
                return PaymentResult(kind="jump", url=payload)
            return PaymentResult(kind="qrcode", url=payload)

    if ok_code:
        _log.warning("xiannv2 下单成功但未匹配到支付地址字段: %s", data)
        return PaymentResult(kind="error", msg="下单失败：未获取到支付地址")

    msg = ""
    for f in ("message", "msg", "error_message", "errorMessage"):
        v = data.get(f)
        if v:
            msg = str(v)
            break
    if not msg and data.get("detail"):
        msg = str(data["detail"])
    return PaymentResult(kind="error", msg="创建订单失败！" + (msg or "未知错误"))


class Xiannv2Payment(BasePayment):
    """仙女支付2（乐事 leshiapi 协议）上游。"""

    meta = PluginMeta(
        name="xiannv2",
        showname="仙女支付2",
        types=["alipay", "wxpay"],
        inputs=[
            {"name": "appurl", "title": "下单接口地址", "type": "input",
             "placeholder": DEFAULT_GATEWAY,
             "note": "留空用默认网关；查询接口按同域名 /api/merchant/query 自动派生"},
            {"name": "appmchid", "title": "商户号（merchantId）", "type": "input",
             "placeholder": "10298", "required": True},
            {"name": "appkey", "title": "商户密钥（secret）", "type": "input",
             "placeholder": "商户后台分配的32位密钥，用于MD5签名", "required": True},
            {"name": "productid", "title": "支付方式编码（payType）", "type": "input",
             "placeholder": "数字编码如 1，不同支付方式不同，需向平台确认", "required": True},
        ],
    )

    # ------------------------------------------------------------

    def _cfg(self, name: str, default: str = "") -> str:
        return str(self.channel.get(name, "") or "").strip() or default

    def _appkey(self) -> str:
        return self._cfg("appkey")

    def _gateway(self) -> str:
        return self._cfg("appurl", DEFAULT_GATEWAY)

    def _query_url(self) -> str:
        gw = self._gateway()
        # 下单 .../api/merchant/getOrder → 查询 .../api/merchant/query
        if gw.endswith("/getOrder"):
            return gw[: -len("getOrder")] + "query"
        return gw.rstrip("/") + "/query"

    async def _post_json(self, url: str, params: dict) -> dict | None:
        """POST JSON，返回解析后的 dict；异常/非 JSON 返回 None 并记日志。"""
        client = http_client.get_http_client()
        self.last_debug = {
            "url": url, "method": "POST",
            "content_type": "application/json;charset=UTF-8",
            "params": {k: ("***" if k == "sign" else v) for k, v in params.items()},
            "http_status": None, "duration_ms": None, "body": None,
        }
        start = time.monotonic()
        try:
            resp = await client.post(
                url, json=params,
                headers={"User-Agent": BROWSER_UA,
                         "Content-Type": "application/json;charset=UTF-8"},
            )
        except Exception as e:
            self.last_debug["duration_ms"] = int((time.monotonic() - start) * 1000)
            self.last_debug["body"] = f"请求异常: {e}"
            _log.warning("xiannv2 请求失败 url=%s: %s", url, e)
            return None
        self.last_debug["duration_ms"] = int((time.monotonic() - start) * 1000)
        self.last_debug["http_status"] = resp.status_code
        self.last_debug["body"] = resp.text[:2000]
        try:
            return resp.json()
        except Exception as e:
            _log.warning("xiannv2 响应解析失败: %s", e)
            return None

    # ------------------------------------------------------------

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        productid = self._cfg("productid")
        if not productid:
            return PaymentResult(kind="error",
                                 msg="当前通道未配置支付方式编码，请联系管理员")

        params = {
            "merchantId": self._cfg("appmchid"),
            "bizOrderNo": ctx.order["trade_no"],
            "money": _money_2dp(ctx.order["realmoney"]),
            "payType": productid,
            "bizNotifyUrl": ctx.notify_url,
        }
        params["sign"] = make_sign(params, self._appkey())

        data = await self._post_json(self._gateway(), params)
        if data is None:
            return PaymentResult(kind="error", msg="请求支付接口失败")
        return _parse_pay_result(data)

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        params = {
            "merchantId": self._cfg("appmchid"),
            "bizOrderNo": ctx.order["trade_no"],
        }
        params["sign"] = make_sign(params, self._appkey())

        data = await self._post_json(self._query_url(), params)
        if not isinstance(data, dict):
            return QueryResult(status="unknown")

        code = str(data.get("code", "") or "")
        if code not in OK_CODES:
            return QueryResult(status="unknown", raw=data)

        body = data.get("data") if isinstance(data.get("data"), dict) else data
        status = "paid" if _is_paid(body) else "unpaid"
        return QueryResult(
            status=status,
            api_trade_no=_pick(body, API_ORDER_FIELDS),
            money=_parse_money(_pick(body, MONEY_FIELDS)),
            raw=data,
        )

    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        """异步回调：验签 + 状态字段白名单判成功，应答纯文本 success/fail。"""
        data = dict(ctx_params or {})
        if "sign" not in data and body:
            import json
            try:
                parsed = json.loads(body)
            except Exception:
                return NotifyVerdict(ok=False, ack="fail")
            if isinstance(parsed, dict):
                data = parsed

        sign = str(data.get("sign", "") or "")
        if not sign or not _verify_sign(data, self._appkey(), sign):
            return NotifyVerdict(ok=False, ack="fail")

        # 商户号核对（回调携带 merchantId 时）
        mch = data.get("merchantId")
        if mch is not None and str(mch) != self._cfg("appmchid"):
            _log.warning("xiannv2 回调商户号不匹配: expected=%s got=%s",
                         self._cfg("appmchid"), mch)
            return NotifyVerdict(ok=False, ack="fail")

        if not _is_paid(data):
            # 验签通过但非支付成功状态：应答 success 让上游停止重发
            return NotifyVerdict(ok=False, ack="success")

        money_str = _pick(data, MONEY_FIELDS)
        if money_str:
            money = _parse_money(money_str)
            if money is None:
                return NotifyVerdict(ok=False, ack="fail")
        else:
            # 回调未携带金额字段：按订单号处理，money=None 交给中心层用订单金额
            money = None

        api_trade_no = _pick(data, API_ORDER_FIELDS)
        return NotifyVerdict(
            ok=True,
            api_trade_no=api_trade_no,
            money=money,
            ack="success",
        )


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = Xiannv2Payment
