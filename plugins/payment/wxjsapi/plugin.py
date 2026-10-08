"""
微信JSAPI支付插件 (Legacy V2)

使用微信支付V2统一下单接口，XML格式请求/响应，MD5签名。
支持 JSAPI 和 Native 两种交易类型。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult

logger = logging.getLogger("plugin.wxjsapi")

WXPAY_UNIFIED_ORDER_URL = "https://api.mch.weixin.qq.com/pay/unifiedorder"
WXPAY_REFUND_URL = "https://api.mch.weixin.qq.com/secapi/pay/refund"
WXPAY_ORDER_QUERY_URL = "https://api.mch.weixin.qq.com/pay/orderquery"
WXPAY_CLOSE_URL = "https://api.mch.weixin.qq.com/pay/closeorder"


class WxJsapiPayment(BasePayment):
    """微信JSAPI支付 (V2统一下单)"""

    def __init__(self, channel: dict):
        super().__init__(channel)
        raw = json.loads(channel.get("config", "{}")) if isinstance(channel.get("config"), str) else channel.get("config", {})
        cfg = {**channel, **raw}

        self.appid: str = cfg.get("appid", "")
        self.mch_id: str = cfg.get("mch_id", cfg.get("mchid", ""))
        self.api_key: str = cfg.get("api_key", "")
        self.notify_url: str = cfg.get("notify_url", "")

    # -- XML 工具 --------------------------------------------------------------

    @staticmethod
    def _dict_to_xml(d: dict) -> str:
        """将字典转为微信支付XML格式"""
        xml_parts = ["<xml>"]
        for k, v in d.items():
            xml_parts.append(f"<{k}><![CDATA[{v}]]></{k}>")
        xml_parts.append("</xml>")
        return "".join(xml_parts)

    @staticmethod
    def _xml_to_dict(xml_str: str) -> dict:
        """将微信支付XML响应解析为字典"""
        root = ET.fromstring(xml_str)
        return {child.tag: child.text or "" for child in root}

    # -- MD5 签名 --------------------------------------------------------------

    def _md5_sign(self, params: dict) -> str:
        """微信支付MD5签名：字典序排列，拼接key，MD5哈希"""
        filtered = {k: v for k, v in params.items() if k != "sign" and v is not None and str(v) != ""}
        sorted_items = sorted(filtered.items(), key=lambda x: x[0])
        sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)
        sign_str += f"&key={self.api_key}"
        return hashlib.md5(sign_str.encode("utf-8")).hexdigest().upper()

    def _verify_sign(self, params: dict) -> bool:
        """验证微信回调签名"""
        sign = params.get("sign", "")
        if not sign:
            return False
        expected = self._md5_sign(params)
        return sign == expected

    # -- HTTP 请求 --------------------------------------------------------------

    def _post_xml(self, url: str, params: dict) -> dict:
        params["sign"] = self._md5_sign(params)
        xml_body = self._dict_to_xml(params)
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(url, content=xml_body.encode("utf-8"),
                                   headers={"Content-Type": "application/xml"})
            resp.raise_for_status()
            return self._xml_to_dict(resp.text)
        except httpx.HTTPError as e:
            logger.error("wxjsapi HTTP error: %s", e)
            raise RuntimeError(f"微信支付网络请求异常: {e}")

    # -- JSAPI 签名 -------------------------------------------------------------

    def _jsapi_pay_sign(self, prepay_id: str) -> dict:
        """生成JSAPI调起支付的参数和签名"""
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        params = {
            "appId": self.appid,
            "timeStamp": ts,
            "nonceStr": nonce,
            "package": f"prepay_id={prepay_id}",
            "signType": "MD5",
        }
        params["paySign"] = self._md5_sign(params)
        return params

    # =========================================================================
    # 接口实现
    # =========================================================================

    def submit(self, ctx: PaymentContext) -> PaymentResult:
        order = ctx.order
        trade_no = order.get("trade_no", "")
        total_fee = int(round(float(order.get("money", 0)) * 100))
        ordername = ctx.ordername or order.get("ordername", "订单支付")
        method = ctx.method or "wechat"

        notify_url = self.notify_url or f"https://{self.channel.get('domain', '')}/pay/wxjsapi/{trade_no}"

        # 统一下单请求参数
        nonce_str = uuid.uuid4().hex
        params = {
            "appid": self.appid,
            "mch_id": self.mch_id,
            "nonce_str": nonce_str,
            "body": ordername,
            "out_trade_no": trade_no,
            "total_fee": str(total_fee),
            "spbill_create_ip": order.get("ip", "127.0.0.1"),
            "notify_url": notify_url,
            "trade_type": "JSAPI",
            "openid": order.get("openid", ""),
        }

        # 根据method决定trade_type
        if method in ("pc", "native", "1"):
            params["trade_type"] = "NATIVE"
            # NATIVE不需要openid
            params.pop("openid", None)
        elif method in ("mobile", "h5", "3"):
            params["trade_type"] = "MWEB"
            params.pop("openid", None)
        else:
            # JSAPI
            if not params.get("openid"):
                raise RuntimeError("JSAPI支付需要用户openid")

        try:
            resp = self._post_xml(WXPAY_UNIFIED_ORDER_URL, params)
        except Exception as e:
            logger.error("wxjsapi unified order failed: %s", e)
            raise

        return_code = resp.get("return_code", "")
        result_code = resp.get("result_code", "")
        if return_code != "SUCCESS" or result_code != "SUCCESS":
            err_msg = resp.get("return_msg") or resp.get("err_code_des") or "下单失败"
            raise RuntimeError(f"微信统一下单失败: {err_msg}")

        # 根据trade_type返回不同的支付信息
        if params["trade_type"] == "NATIVE":
            code_url = resp.get("code_url", "")
            return PaymentResult(pay_type="qrcode", pay_info=code_url)
        elif params["trade_type"] == "MWEB":
            mweb_url = resp.get("mweb_url", "")
            return PaymentResult(pay_type="jump", pay_info=mweb_url)
        else:
            # JSAPI
            prepay_id = resp.get("prepay_id", "")
            jsapi_params = self._jsapi_pay_sign(prepay_id)
            return PaymentResult(pay_type="jsapi", pay_info=json.dumps(jsapi_params, ensure_ascii=False))

    # -- 异步通知 ---------------------------------------------------------------

    def notify(self, params: dict, body: bytes = b"") -> dict | None:
        """
        处理微信支付V2异步回调通知。

        body 为原始XML bytes。
        """
        try:
            if not body:
                logger.warning("wxjsapi notify: empty body")
                return None

            xml_str = body.decode("utf-8") if isinstance(body, bytes) else body
            data = self._xml_to_dict(xml_str)

            # 验证签名
            if not self._verify_sign(data):
                logger.error("wxjsapi notify: 签名验证失败")
                return None

            return_code = data.get("return_code", "")
            result_code = data.get("result_code", "")
            if return_code != "SUCCESS" or result_code != "SUCCESS":
                logger.info("wxjsapi notify: 交易未成功 return=%s result=%s", return_code, result_code)
                return None

            total_fee = int(data.get("total_fee", "0"))
            return {
                "api_trade_no": data.get("transaction_id", ""),
                "money": total_fee / 100.0,
                "buyer": data.get("openid", ""),
            }
        except Exception as e:
            logger.exception("wxjsapi notify处理异常: %s", e)
            return None

    # -- 退款 -------------------------------------------------------------------

    def refund(self, order: dict, refund_no: str, money: float) -> dict:
        total_fee = int(round(float(order.get("money", 0)) * 100))
        refund_fee = int(round(money * 100))
        nonce_str = uuid.uuid4().hex

        params = {
            "appid": self.appid,
            "mch_id": self.mch_id,
            "nonce_str": nonce_str,
            "out_trade_no": order.get("trade_no", ""),
            "out_refund_no": refund_no,
            "total_fee": str(total_fee),
            "refund_fee": str(refund_fee),
            "op_user_id": self.mch_id,
        }

        try:
            resp = self._post_xml(WXPAY_REFUND_URL, params)
            result_code = resp.get("result_code", "")
            if result_code == "SUCCESS":
                return {
                    "code": 0,
                    "refund_id": resp.get("refund_id", ""),
                    "out_refund_no": resp.get("out_refund_no", refund_no),
                }
            return {
                "code": -1,
                "msg": resp.get("err_code_des", resp.get("return_msg", "退款失败")),
            }
        except Exception as e:
            logger.error("wxjsapi refund failed: %s", e)
            return {"code": -1, "msg": str(e)}

    # -- 关闭订单 ---------------------------------------------------------------

    def close(self, order: dict) -> bool:
        trade_no = order.get("trade_no", "")
        if not trade_no:
            return False
        params = {
            "appid": self.appid,
            "mch_id": self.mch_id,
            "out_trade_no": trade_no,
            "nonce_str": uuid.uuid4().hex,
        }
        try:
            resp = self._post_xml(WXPAY_CLOSE_URL, params)
            return resp.get("result_code") == "SUCCESS"
        except Exception as e:
            logger.error("wxjsapi close failed: %s", e)
            return False

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True
