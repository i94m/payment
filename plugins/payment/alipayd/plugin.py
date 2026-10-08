"""
支付宝直付通模式 (alipayd)

商家使用自己的 appid 直接收款，支持 SMID（子商户）路由。
支持: 当面付(扫码), 手机网站, 电脑网站, 转账。
RSA2 (SHA256WithRSA) 签名。
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult

logger = logging.getLogger("plugin.alipayd")

ALIPAY_GATEWAY = "https://openapi.alipay.com/gateway.do"


class AlipayDirectPayment(BasePayment):
    """支付宝直付通模式支付插件"""

    def __init__(self, channel: dict):
        super().__init__(channel)
        raw = json.loads(channel.get("config", "{}")) if isinstance(channel.get("config"), str) else channel.get("config", {})
        cfg = {**channel, **raw}

        self.appid: str = cfg.get("appid", "")
        self.private_key: str = cfg.get("private_key", cfg.get("appsecret", ""))
        self.alipay_public_key: str = cfg.get("alipay_public_key", cfg.get("publickey", ""))
        self.smid: str = cfg.get("smid", "")
        self.notify_url: str = cfg.get("notify_url", "")

    # -- RSA2 签名/验签 --------------------------------------------------------

    @staticmethod
    def _format_pem(key_str: str, key_type: str = "private") -> bytes:
        key_str = key_str.strip()
        if "-----BEGIN" in key_str:
            return key_str.encode("utf-8")
        lines = [key_str[i:i + 64] for i in range(0, len(key_str), 64)]
        body = "\n".join(lines)
        if key_type == "private":
            return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----".encode("utf-8")
        return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----".encode("utf-8")

    def _sign_params(self, params: dict) -> str:
        """RSA2签名: 过滤sign/sign_type，字典序排列，拼接后签名"""
        filtered = {k: v for k, v in params.items()
                    if k not in ("sign", "sign_type") and v is not None and str(v) != ""}
        sorted_items = sorted(filtered.items(), key=lambda x: x[0])
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)

        pk = serialization.load_pem_private_key(self._format_pem(self.private_key, "private"), password=None)
        sig = pk.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode("utf-8")

    def _verify_notify(self, params: dict) -> bool:
        """验证支付宝异步通知RSA2签名"""
        sign = params.get("sign", "")
        if not sign:
            return False
        filtered = {k: v for k, v in params.items()
                    if k not in ("sign", "sign_type") and v is not None and str(v) != ""}
        sorted_items = sorted(filtered.items(), key=lambda x: x[0])
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)

        try:
            pub_key = serialization.load_pem_public_key(self._format_pem(self.alipay_public_key, "public"))
            pub_key.verify(
                base64.b64decode(sign),
                sign_str.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except Exception as e:
            logger.error("Alipay notify signature verification failed: %s", e)
            return False

    # -- 构建请求参数 -----------------------------------------------------------

    def _build_common_params(self, method: str, biz_content: dict, notify_url: str = "",
                             return_url: str = "") -> dict:
        params = {
            "app_id": self.appid,
            "method": method,
            "charset": "utf-8",
            "sign_type": "RSA2",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False),
        }
        if notify_url:
            params["notify_url"] = notify_url
        if return_url:
            params["return_url"] = return_url
        params["sign"] = self._sign_params(params)
        return params

    def _build_form_html(self, params: dict) -> str:
        """构建自动提交的HTML表单"""
        items = [f'<input type="hidden" name="{k}" value=\'{v}\' />' for k, v in params.items()]
        return (
            f'<form id="alipaysubmit" name="alipaysubmit" '
            f'action="{ALIPAY_GATEWAY}?charset=utf-8" method="POST">'
            f'{"".join(items)}'
            f'<input type="submit" value="提交" style="display:none;" />'
            f'</form>'
            f'<script>document.forms["alipaysubmit"].submit();</script>'
        )

    def _execute_api(self, method: str, biz_content: dict, notify_url: str = "") -> dict:
        """调用支付宝OpenAPI并解析JSON响应"""
        params = self._build_common_params(method, biz_content, notify_url)
        try:
            resp = httpx.post(ALIPAY_GATEWAY, data=params, timeout=30)
            resp.raise_for_status()
            result = resp.json()
            resp_key = method.replace(".", "_") + "_response"
            body = result.get(resp_key, {})
            if body.get("code") == "10000":
                return {"code": 0, "data": body}
            return {"code": -1, "msg": body.get("sub_msg") or body.get("msg") or "接口调用失败"}
        except Exception as e:
            logger.error("Alipay API error: %s", e)
            return {"code": -1, "msg": str(e)}

    def _build_biz_with_smid(self, order: dict, ordername: str = "订单支付") -> dict:
        """构建带 smid 子商户路由的 biz_content"""
        biz = {
            "out_trade_no": order.get("trade_no", ""),
            "total_amount": str(order.get("money", "0")),
            "subject": order.get("name", ordername),
        }
        if self.smid:
            biz["extend_params"] = {"sys_service_provider_id": self.smid}
        return biz

    # =========================================================================
    # 接口实现
    # =========================================================================

    def submit(self, ctx: PaymentContext) -> PaymentResult:
        order = ctx.order
        trade_no = order.get("trade_no", "")
        money = order.get("money", "0")
        ordername = ctx.ordername or order.get("ordername", "订单支付")
        method = ctx.method or "pc"

        notify_url = self.notify_url or f"https://{self.channel.get('domain', '')}/pay/alipayd/{trade_no}"
        return_url = order.get("return_url", "")

        biz = {
            "out_trade_no": trade_no,
            "total_amount": str(money),
            "subject": ordername,
        }
        # 子商户路由
        if self.smid:
            biz["extend_params"] = {"sys_service_provider_id": self.smid}

        if method in ("pc", "3"):
            return self._submit_page(biz, notify_url, return_url)
        elif method in ("mobile", "wap", "2"):
            return self._submit_wap(biz, notify_url, return_url)
        else:
            return self._submit_page(biz, notify_url, return_url)

    def _submit_page(self, biz: dict, notify_url: str, return_url: str) -> PaymentResult:
        """电脑网站支付: alipay.trade.page.pay"""
        biz["product_code"] = "FAST_INSTANT_TRADE_PAY"
        params = self._build_common_params("alipay.trade.page.pay", biz, notify_url, return_url)
        html = self._build_form_html(params)
        return PaymentResult(pay_type="html", pay_info=html, expires_in=600)

    def _submit_wap(self, biz: dict, notify_url: str, return_url: str) -> PaymentResult:
        """手机网站支付: alipay.trade.wap.pay"""
        biz["product_code"] = "QUICK_WAP_WAY"
        params = self._build_common_params("alipay.trade.wap.pay", biz, notify_url, return_url)
        html = self._build_form_html(params)
        return PaymentResult(pay_type="html", pay_info=html, expires_in=600)

    # -- 异步通知 ---------------------------------------------------------------

    def notify(self, params: dict, body: bytes = b"") -> dict | None:
        """处理支付宝异步通知，验证RSA2签名，提取交易信息"""
        if not self._verify_notify(params):
            logger.error("Alipay notify signature verification failed")
            return None

        trade_status = params.get("trade_status", "")
        if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            logger.info("Alipay notify ignored, trade_status=%s", trade_status)
            return None

        trade_no = params.get("trade_no", "")
        total_amount = params.get("total_amount", "0")
        buyer_logon_id = params.get("buyer_logon_id", "")

        if not trade_no:
            logger.warning("Alipay notify missing trade_no")
            return None

        return {
            "api_trade_no": trade_no,
            "money": float(total_amount),
            "buyer": buyer_logon_id,
        }

    # -- 同步返回 ---------------------------------------------------------------

    def return_handler(self, params: dict) -> dict | None:
        if self._verify_notify(params):
            return {
                "trade_no": params.get("out_trade_no", ""),
                "api_trade_no": params.get("trade_no", ""),
            }
        return None

    # -- 退款 -------------------------------------------------------------------

    def refund(self, order: dict, refund_no: str, money: float) -> dict:
        biz = {
            "out_trade_no": order.get("trade_no", ""),
            "refund_amount": str(money),
            "out_request_no": refund_no,
        }
        result = self._execute_api("alipay.trade.refund", biz)
        if result["code"] == 0:
            return {"code": 0, "msg": "退款成功", "refund_fee": result["data"].get("refund_fee", str(money))}
        return {"code": -1, "msg": result.get("msg", "退款失败")}

    # -- 转账 -------------------------------------------------------------------

    def transfer(self, data: dict) -> dict:
        """支付宝转账: alipay.fund.trans.uni.transfer"""
        biz = {
            "out_biz_no": data.get("trade_no", data.get("biz_no", "")),
            "trans_amount": str(data.get("money", "0")),
            "product_code": "TRANS_ACCOUNT_NO_PWD",
            "biz_scene": "DIRECT_TRANSFER",
            "payee_info": {
                "identity": data.get("payee_account", ""),
                "identity_type": data.get("payee_type", "ALIPAY_LOGON_ID"),
            },
            "remark": data.get("remark", "转账"),
        }
        result = self._execute_api("alipay.fund.trans.uni.transfer", biz)
        if result["code"] == 0:
            resp_data = result["data"]
            return {
                "status": 1,
                "orderid": resp_data.get("order_id", ""),
                "paydate": resp_data.get("trans_date", ""),
            }
        return {"status": 0, "msg": result.get("msg", "转账失败")}

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True
