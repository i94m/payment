"""高盛支付上游插件（sk 协议）。

协议与上游官方文档（sk_api_doc.html）核对（线上实测）：

- 下单：POST JSON 到 {appurl}（形如 https://网关/pay/skpay.php?fun=createOrder）
  参数 merchant/orderNo/currentTime/payType/amount/notifyUrl/returnUrl/version="1.0"/sign
- 签名（sk 规则，注意与 epay 插件的拼串不同）：参数名 ASCII 升序 "k=v" 用 & 连接，
  sign 与空值不参与，拼接串末尾直接追加商户密钥（无 &、无 key= 前缀），MD5 小写
- 金额：元，固定两位小数字符串（"10.00"），签名与请求体字面值一致
- 响应：code==200 成功，data.payUrl 支付跳转链接，data.sys_order_id 平台单号
- 回调：JSON POST（merchant/sys_order_id/orderNo/amount/timestamp/noncr/orderState/sign），
  orderState: 1待支付 2支付成功 3已关闭；应答纯文本小写 success/fail
- 查单：?fun=queryOrder 仅支持平台单号 sys_order_id（不支持商户单号），
  本地无 api_trade_no 时返回 unknown，交由补单 cron 下轮再试
- 退款：上游协议未提供，不支持

注意：网关在 Cloudflare 之后，必须带浏览器 UA，否则 403（error code 1010）。
金额与订单号的业务比对由 services/order.process_paid_order 统一执行。
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from decimal import Decimal, InvalidOperation

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

DEFAULT_GATEWAY = "https://api.suvip.shop/pay/skpay.php?fun=createOrder"
# 网关 Cloudflare 拦截非浏览器 UA（403 / error code 1010），必须伪装浏览器
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _sign(data: dict, appkey: str) -> str:
    """sk 签名：ASCII 升序 k=v 用 & 连接（空值与 sign 不参与）+ 直接拼密钥 → md5 小写。"""
    keys = sorted(
        k for k, v in data.items()
        if k != "sign"
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    content = "&".join(f"{k}={data[k]}" for k in keys)
    return hashlib.md5((content + appkey).encode("utf-8")).hexdigest()


def _verify_sign(data: dict, appkey: str, sign: str) -> bool:
    # 上游签名比对大小写不敏感（对齐老实现 strcasecmp）
    return hmac.compare_digest(_sign(data, appkey), sign.strip().lower())


def _amount(value) -> str:
    """元，固定两位小数字符串（"10.00"）。"""
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def _extract_error(data: dict) -> str:
    """上游错误文案：外层 msg 常为笼统的 "fail"，真实原因嵌在 data.remark（JSON 串）。"""
    msg = str(data.get("msg") or "上游下单失败")
    inner = data.get("data")
    if isinstance(inner, dict):
        remark = str(inner.get("remark") or "")
        if remark:
            detail = remark
            try:
                parsed = json.loads(remark)
                if isinstance(parsed, dict) and parsed.get("msg"):
                    detail = str(parsed["msg"])
            except Exception:
                pass
            return f"{msg}（{detail}）"
    return msg


class GaoshengPayment(BasePayment):
    """高盛支付（sk 协议）上游。"""

    meta = PluginMeta(
        name="gaosheng",
        showname="高盛支付",
        types=["alipay", "wxpay"],
        inputs=[
            {"name": "appurl", "title": "下单网关地址", "type": "input",
             "placeholder": DEFAULT_GATEWAY,
             "note": "留空用默认网关；查询/余额接口按同域名 ?fun= 自动派生"},
            {"name": "appmchid", "title": "商户号（merchant）", "type": "input", "required": True},
            {"name": "appkey", "title": "商户密钥", "type": "input", "required": True},
            {"name": "productid", "title": "支付产品编码（payType）", "type": "input",
             "note": "如支付宝 6237，不同支付方式编码不同，需向平台确认"},
        ],
    )

    def _merchant(self) -> str:
        mch = str(self.channel.get("appmchid", "") or "").strip()
        if not mch:
            raise ValueError("通道未配置商户号(appmchid)")
        return mch

    def _appkey(self) -> str:
        return str(self.channel.get("appkey", "") or "")

    def _api_url(self, fun: str) -> str:
        base = str(self.channel.get("appurl", "") or "").strip() or DEFAULT_GATEWAY
        root = base.split("?", 1)[0]  # 去掉 ?fun=xxx，统一按 fun 重建
        if not root.startswith(("http://", "https://")):
            root = "https://" + root
        return root + "?fun=" + fun

    async def _post(self, fun: str, params: dict) -> dict | None:
        # 调用期取共享 client（测试对 http_client 模块打补丁生效）
        client = http_client.get_http_client()
        url = self._api_url(fun)
        # 记录本次上游交互快照（管理端通道测试的调试弹窗用）；params 含 sign，不含密钥
        self.last_debug = {
            "url": url, "method": "POST", "content_type": "application/json; charset=utf-8",
            "params": params, "http_status": None, "duration_ms": None, "body": None,
        }
        start = time.monotonic()
        try:
            resp = await client.post(
                url,
                json=params,
                headers={"User-Agent": BROWSER_UA},
            )
        except Exception as e:
            self.last_debug["duration_ms"] = int((time.monotonic() - start) * 1000)
            self.last_debug["body"] = f"请求异常: {e}"
            _log.warning("gaosheng %s 请求失败: %s", fun, e)
            return None
        self.last_debug["duration_ms"] = int((time.monotonic() - start) * 1000)
        self.last_debug["http_status"] = resp.status_code
        self.last_debug["body"] = resp.text[:2000]
        try:
            return resp.json()
        except Exception as e:
            _log.warning("gaosheng %s 响应解析失败: %s", fun, e)
            return None

    # ------------------------------------------------------------

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        params = {
            "merchant": self._merchant(),
            "orderNo": ctx.order["trade_no"],
            "currentTime": str(int(time.time())),
            "payType": str(self.channel.get("productid", "") or "").strip(),
            "amount": _amount(ctx.order["realmoney"]),
            "notifyUrl": ctx.notify_url,
            "returnUrl": ctx.return_url,
            "version": "1.0",
        }
        params["sign"] = _sign(params, self._appkey())

        data = await self._post("createOrder", params)
        if data is None:
            return PaymentResult(kind="error", msg="上游通道连接失败")

        if str(data.get("code")) == "200":
            pay_url = str((data.get("data") or {}).get("payUrl") or "")
            if pay_url:
                return PaymentResult(kind="jump", url=pay_url)
            return PaymentResult(kind="error", msg="上游未返回支付链接")
        return PaymentResult(kind="error", msg=_extract_error(data))

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        """queryOrder 仅支持平台单号 sys_order_id。"""
        sys_order_id = str(ctx.order.get("api_trade_no") or "")
        if not sys_order_id:
            return QueryResult(status="unknown")

        params = {
            "merchant": self._merchant(),
            "sys_order_id": sys_order_id,
            "timestamp": str(int(time.time())),
            "noncr": "".join(secrets.choice("0123456789") for _ in range(6)),
        }
        params["sign"] = _sign(params, self._appkey())

        data = await self._post("queryOrder", params)
        if not data or str(data.get("code")) != "200":
            return QueryResult(status="unknown", raw=data or {})

        body = data.get("data") or {}
        state = str(body.get("orderState", ""))
        status = {"1": "unpaid", "2": "paid", "3": "failed"}.get(state, "unknown")
        money = None
        try:
            money = Decimal(str(body.get("amount")))
        except (InvalidOperation, TypeError, ValueError):
            pass
        return QueryResult(
            status=status,
            api_trade_no=str(body.get("sys_order_id") or sys_order_id),
            money=money,
            raw=data,
        )

    # ------------------------------------------------------------

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        """异步回调：JSON POST 验签 + orderState==2 判成功，应答纯文本 success/fail。"""
        data = dict(ctx_params or {})
        if "sign" not in data and body:
            try:
                parsed = json.loads(body)
            except Exception:
                return NotifyVerdict(ok=False, ack="fail")
            if isinstance(parsed, dict):
                data = parsed

        sign = str(data.get("sign", ""))
        if not sign or not _verify_sign(data, self._appkey(), sign):
            return NotifyVerdict(ok=False, ack="fail")

        if str(data.get("orderState", "")) != "2":
            # 验签通过但非支付成功（1 待支付 / 3 已关闭）：应答 success 让上游停止重发
            return NotifyVerdict(ok=False, ack="success")

        try:
            money = Decimal(str(data.get("amount")))
        except (InvalidOperation, TypeError, ValueError):
            return NotifyVerdict(ok=False, ack="fail")

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(data.get("sys_order_id") or ""),
            money=money,
            ack="success",
        )


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = GaoshengPayment
