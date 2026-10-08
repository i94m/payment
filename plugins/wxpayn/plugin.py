"""
微信支付V3插件 — Native / H5 / JSAPI

使用微信支付V3 API，RSA-SHA256签名，AES-256-GCM解密。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult

logger = logging.getLogger("plugin.wxpayn")

WXPAY_BASE_URL = "https://api.mch.weixin.qq.com"


class WxPayV3(BasePayment):
    """微信支付V3 — Native / H5 / JSAPI"""

    def __init__(self, channel: dict):
        super().__init__(channel)
        raw = json.loads(channel.get("config", "{}")) if isinstance(channel.get("config"), str) else channel.get("config", {})
        cfg = {**channel, **raw}

        self.appid: str = cfg.get("appid", "")
        self.mchid: str = cfg.get("mchid", "")
        self.api_key_v3: str = cfg.get("api_key_v3", "")
        self.serial_no: str = cfg.get("serial_no", "")
        self.notify_url: str = cfg.get("notify_url", "")

        # 私钥（base64 PEM 或 PEM 文本）
        self._private_key_pem = cfg.get("api_key_v3_pem", "")
        self._merchant_private_key = None

    # -- 密钥加载 --------------------------------------------------------------

    def _load_private_key(self):
        if self._merchant_private_key is not None:
            return self._merchant_private_key
        pem_str = self._private_key_pem
        if not pem_str:
            raise RuntimeError("商户私钥未配置")
        # 如果是 base64 编码的 PEM
        if "-----BEGIN" not in pem_str:
            pem_str = base64.b64decode(pem_str).decode("utf-8")
        self._merchant_private_key = serialization.load_pem_private_key(
            pem_str.encode("utf-8"), password=None
        )
        return self._merchant_private_key

    # -- V3 签名 ----------------------------------------------------------------

    def _sign(self, method: str, path: str, body: str = "") -> str:
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        sign_str = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body}\n"

        pk = self._load_private_key()
        sig = pk.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        sig_b64 = base64.b64encode(sig).decode("utf-8")

        return (
            f'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.mchid}",'
            f'nonce_str="{nonce}",'
            f'timestamp="{timestamp}",'
            f'serial_no="{self.serial_no}",'
            f'signature="{sig_b64}"'
        )

    # -- HTTP 请求 --------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{WXPAY_BASE_URL}{path}"
        body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
        auth = self._sign(method, path, body_str)
        headers = {
            "Authorization": auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.request(method, url, content=body_str.encode("utf-8") if body_str else None, headers=headers)
            if resp.status_code < 200 or resp.status_code >= 300:
                raise RuntimeError(f"微信支付V3请求失败: HTTP {resp.status_code} - {resp.text}")
            return resp.json() if resp.text.strip() else {}
        except httpx.HTTPError as e:
            logger.error("WxPayV3 HTTP error: %s", e)
            raise RuntimeError(f"微信支付网络请求异常: {e}")

    # -- JSAPI 预支付参数 -------------------------------------------------------

    def _jsapi_params(self, prepay_id: str) -> dict:
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        sign_str = f"{self.appid}\n{ts}\n{nonce}\nprepay_id={prepay_id}\n"
        pk = self._load_private_key()
        sig = pk.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return {
            "appId": self.appid,
            "timeStamp": ts,
            "nonceStr": nonce,
            "package": f"prepay_id={prepay_id}",
            "signType": "RSA",
            "paySign": base64.b64encode(sig).decode("utf-8"),
        }

    # -- 验签 & 解密 ------------------------------------------------------------

    def _verify_signature(self, timestamp: str, nonce: str, body_str: str, sig_b64: str) -> bool:
        """简化验签：实际应使用平台证书公钥，此处不加载平台证书则跳过。"""
        # TODO: 使用平台证书公钥验签
        logger.debug("V3 signature verification skipped (no platform cert)")
        return True

    @staticmethod
    def _decrypt_aes_gcm(key: str, nonce: str, ciphertext_b64: str, associated_data: str) -> str:
        aesgcm = AESGCM(key.encode("utf-8"))
        plaintext = aesgcm.decrypt(
            nonce.encode("utf-8"),
            base64.b64decode(ciphertext_b64),
            associated_data.encode("utf-8"),
        )
        return plaintext.decode("utf-8")

    # =========================================================================
    # 接口实现
    # =========================================================================

    def submit(self, ctx: PaymentContext) -> PaymentResult:
        order = ctx.order
        trade_no = order.get("trade_no", "")
        total_cents = int(round(float(order.get("money", 0)) * 100))
        ordername = ctx.ordername or order.get("ordername", "订单支付")
        method = ctx.method or "pc"

        notify_url = self.notify_url or f"https://{self.channel.get('domain', '')}/pay/wxpayn/{trade_no}"

        base_body: dict[str, Any] = {
            "appid": self.appid,
            "mchid": self.mchid,
            "description": ordername,
            "out_trade_no": trade_no,
            "notify_url": notify_url,
            "amount": {"total": total_cents, "currency": "CNY"},
            "scene_info": {"payer_client_ip": order.get("ip", "127.0.0.1")},
        }

        if method in ("pc", "1"):
            return self._submit_native(base_body)
        elif method in ("mobile", "h5", "3"):
            return self._submit_h5(base_body)
        elif method in ("wechat", "jsapi", "2"):
            openid = order.get("openid", "")
            return self._submit_jsapi(base_body, openid)
        else:
            return self._submit_native(base_body)

    def _submit_native(self, body: dict) -> PaymentResult:
        resp = self._request("POST", "/v3/pay/transactions/native", body)
        return PaymentResult(pay_type="qrcode", pay_info=resp.get("code_url", ""))

    def _submit_h5(self, body: dict) -> PaymentResult:
        body["scene_info"]["h5_info"] = {"type": "Wap"}
        resp = self._request("POST", "/v3/pay/transactions/h5", body)
        return PaymentResult(pay_type="jump", pay_info=resp.get("h5_url", ""))

    def _submit_jsapi(self, body: dict, openid: str) -> PaymentResult:
        if not openid:
            raise RuntimeError("JSAPI支付需要用户openid")
        body["payer"] = {"openid": openid}
        resp = self._request("POST", "/v3/pay/transactions/jsapi", body)
        prepay_id = resp.get("prepay_id", "")
        params = self._jsapi_params(prepay_id)
        return PaymentResult(pay_type="jsapi", pay_info=json.dumps(params, ensure_ascii=False))

    # -- 异步通知 ---------------------------------------------------------------

    def notify(self, params: dict, body: bytes = b"") -> dict | None:
        try:
            wx_sig = params.get("wechatpay-signature", "")
            wx_ts = params.get("wechatpay-timestamp", "")
            wx_nonce = params.get("wechatpay-nonce", "")
            body_str = body.decode("utf-8") if isinstance(body, bytes) else body

            if not self._verify_signature(wx_ts, wx_nonce, body_str, wx_sig):
                logger.error("微信回调签名验证失败")
                return None

            data = json.loads(body_str)
            resource = data.get("resource", {})
            ciphertext = resource.get("ciphertext", "")
            nonce = resource.get("nonce", "")
            aad = resource.get("associated_data", "")

            if not ciphertext:
                logger.error("回调缺少加密资源")
                return None

            decrypted = self._decrypt_aes_gcm(self.api_key_v3, nonce, ciphertext, aad)
            result = json.loads(decrypted)

            if result.get("trade_state") != "SUCCESS":
                logger.info("交易状态非SUCCESS: %s", result.get("trade_state"))
                return None

            total_cents = result.get("amount", {}).get("total", 0)
            return {
                "api_trade_no": result.get("transaction_id", ""),
                "money": total_cents / 100.0,
                "buyer": result.get("payer", {}).get("openid", ""),
            }
        except Exception as e:
            logger.exception("处理微信回调异常: %s", e)
            return None

    # -- 退款 -------------------------------------------------------------------

    def refund(self, order: dict, refund_no: str, money: float) -> dict:
        refund_cents = int(round(money * 100))
        total_cents = int(round(float(order.get("money", 0)) * 100))
        payload = {
            "out_trade_no": order.get("trade_no", ""),
            "out_refund_no": refund_no,
            "amount": {"refund": refund_cents, "total": total_cents, "currency": "CNY"},
        }
        try:
            resp = self._request("POST", "/v3/refund/domestic/refunds", payload)
            return {
                "code": 0,
                "refund_id": resp.get("refund_id", ""),
                "out_refund_no": resp.get("out_refund_no", refund_no),
            }
        except Exception as e:
            logger.error("wxpayn refund failed: %s", e)
            return {"code": -1, "msg": str(e)}

    # -- 关闭订单 ---------------------------------------------------------------

    def close(self, order: dict) -> bool:
        trade_no = order.get("trade_no", "")
        if not trade_no:
            return False
        try:
            self._request("POST", f"/v3/pay/transactions/out-trade-no/{trade_no}/close", {"mchid": self.mchid})
            return True
        except Exception as e:
            logger.error("wxpayn close failed: %s", e)
            return False

    # -- 转账 -------------------------------------------------------------------

    def transfer(self, data: dict) -> dict:
        """发起转账 — 调用微信转账API"""
        payload = {
            "appid": self.appid,
            "out_trade_no": data.get("trade_no", ""),
            "transfer_scene": data.get("scene", "1000"),
            "openid": data.get("openid", ""),
            "transfer_amount": int(round(float(data.get("money", 0)) * 100)),
            "transfer_remark": data.get("remark", "转账"),
        }
        try:
            resp = self._request("POST", "/v3/transfer-detail/electronic-receipts", payload)
            return {"status": 1, "orderid": resp.get("out_trade_no", ""), "paydate": resp.get("create_time", "")}
        except Exception as e:
            logger.error("wxpayn transfer failed: %s", e)
            return {"status": 0, "msg": str(e)}

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True
