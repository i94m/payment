"""立鼎支付（liding）插件 — MD5（大写）签名 + 表单下单 + 表单回调。

移植自老 epay 项目 `plugins/liding/liding_plugin.php`，协议要点：

- 下单：POST {appurl}，`application/x-www-form-urlencoded`
  必填：mchId（商户号）/ productId（通道编码）/ mchOrderNo / amount（分，整数）/
  currency（cny）/ notifyUrl / subject / body / reqTime（`yyyyMMddHHmmss`）/ version（1.0）
  可选：clientIp / returnUrl
- 响应：`retCode == "0"`，按 payMethod 取支付信息：
  `formJump` → payJumpUrl / payUrl（跳转）；`codeImg` → codeUrl（扫码内容）或
  codeImgUrl（二维码图片地址，按图片页展示）；`wxApp` / `alipayApp` / `wxJSApi` 不支持
- 回调：form-urlencoded，验签后 `status` 为 2（支付成功）或 3（业务处理完成）视为成功，
  金额单位分（amount / 100），应答 `success`（小写）/ `FAIL`
- 同步回跳：GET 携带同样字段 + sign，status 2 / 3 视为已支付

签名规则：参数按 key ASCII 升序，按 `k=v&` 逐项拼接（跳过 sign 与空值，`"0"` 参与），
末尾拼 `key=密钥`，MD5 **大写**。

金额业务比对由 services/order.process_paid_order 统一执行，插件只报数。
"""
import hashlib
import html
import logging
from datetime import datetime
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
    """签名：key 升序 `k=v&` 拼接 + `key=密钥`，MD5 大写。"""
    keys = sorted(
        k for k, v in params.items()
        if k != "sign"
        and v is not None
        and v != ""
        and not isinstance(v, (list, dict))
    )
    content = "&".join(f"{k}={params[k]}" for k in keys) + "&key=" + key
    return hashlib.md5(content.encode("utf-8")).hexdigest().upper()


def _money_to_cents(value) -> int:
    """元 → 分（整数）。"""
    return int((Decimal(str(value)) * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _cents_to_money(value) -> Decimal | None:
    """分 → 元；解析失败返回 None（由调用方显式拒绝，不放行）。"""
    try:
        return (Decimal(str(value)) / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _image_page(img_url: str, subject: str) -> str:
    """二维码图片地址 → 自包含图片页（收银台 qrcode 类型只收扫码内容，不能直接塞图片 URL）。"""
    return (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(subject or '扫码支付')}</title></head>"
        '<body style="margin:0;display:flex;flex-direction:column;align-items:center;'
        'justify-content:center;min-height:100vh;background:#f7f7f8;font-family:sans-serif">'
        f'<img src="{html.escape(img_url, quote=True)}" alt="支付二维码" '
        'style="width:240px;height:240px;background:#fff;padding:8px;border-radius:8px">'
        "<div style=\"margin-top:12px;color:#666\">请使用对应 App 扫码支付</div>"
        "</body></html>"
    )


class LidingPayment(BasePayment):
    """立鼎支付（MD5 大写签名，form 协议）。"""

    meta = PluginMeta(
        name="liding",
        showname="立鼎支付",
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
        subject = str(ctx.order.get("subject") or "")
        params = {
            "mchId": self._cfg("appid"),
            "productId": self._cfg("productid"),
            "mchOrderNo": ctx.order["trade_no"],
            "amount": _money_to_cents(ctx.order["realmoney"]),
            "currency": "cny",
            "notifyUrl": ctx.notify_url,
            "subject": subject,
            "body": subject,
            "reqTime": datetime.now().strftime("%Y%m%d%H%M%S"),
            "version": "1.0",
        }
        if ctx.client_ip:
            params["clientIp"] = ctx.client_ip
        if ctx.return_url:
            params["returnUrl"] = ctx.return_url
        params["sign"] = _make_sign(params, self._cfg("appkey"))

        try:
            resp = await get_http_client().post(self._api_url(), data=params)
            body = resp.json()
        except Exception as e:
            _log.warning("liding 下单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        if not isinstance(body, dict):
            return PaymentResult(kind="error", msg="上游返回数据解析失败")

        if str(body.get("retCode")) != "0":
            return PaymentResult(kind="error",
                                 msg=str(body.get("retMsg") or "上游下单失败"))

        pay_method = str(body.get("payMethod") or "")
        jump_url = str(body.get("payJumpUrl") or body.get("payUrl") or "")
        code_url = str(body.get("codeUrl") or "")
        code_img = str(body.get("codeImgUrl") or "")

        if pay_method in ("wxApp", "alipayApp", "wxJSApi"):
            return PaymentResult(kind="error", msg="暂不支持该支付方式")

        if pay_method == "codeImg" and code_url:
            return PaymentResult(kind="qrcode", url=code_url)
        if jump_url:
            return PaymentResult(kind="jump", url=jump_url)
        if code_url:
            return PaymentResult(kind="qrcode", url=code_url)
        if code_img:
            if code_img.startswith(("http://", "https://")):
                return PaymentResult(kind="html", html=_image_page(code_img, subject))
            return PaymentResult(kind="qrcode", url=code_img)
        return PaymentResult(kind="error", msg="未返回支付链接")

    # ------------------------------------------------------------

    def _verify(self, ctx_params: dict) -> bool:
        sign = str(ctx_params.get("sign", "") or "").strip()
        if not sign:
            return False
        return _make_sign(ctx_params, self._cfg("appkey")) == sign.upper()

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        if not self._verify(ctx_params):
            return NotifyVerdict(ok=False, ack="FAIL")

        # status: -2 已关闭 / 0 已生成 / 1 支付中 / 2 支付成功 / 3 业务处理完成 / 4 已退款
        if str(ctx_params.get("status", "")) not in ("2", "3"):
            # 验签通过但非成功状态：应答 success 让上游停止重发
            return NotifyVerdict(ok=False, ack="success")

        money = _cents_to_money(ctx_params.get("amount"))
        if money is None:
            return NotifyVerdict(ok=False, ack="FAIL")

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(ctx_params.get("channelOrderNo")
                             or ctx_params.get("payOrderId") or ""),
            money=money,
            ack="success",
        )

    async def return_handler(self, ctx_params: dict) -> dict | None:
        if not self._verify(ctx_params):
            return None
        if str(ctx_params.get("status", "")) not in ("2", "3"):
            return None
        money = _cents_to_money(ctx_params.get("amount"))
        if money is None:
            return None
        return {
            "status": "paid",
            "api_trade_no": str(ctx_params.get("channelOrderNo")
                                or ctx_params.get("payOrderId") or ""),
            "out_trade_no": str(ctx_params.get("mchOrderNo") or ""),
            "money": str(money),
        }

    # ------------------------------------------------------------

    async def query(self, ctx: PaymentContext) -> QueryResult:
        """未对接查单接口（老插件同样没有），一律 unknown 交给人工/回调补单。"""
        return QueryResult(status="unknown")


# 注册类（PluginRegistry 按基类子类发现）
plugin_class = LidingPayment
