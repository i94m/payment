"""抖音支付插件 — 支持小程序支付和H5支付"""
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Optional

import httpx

from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult

logger = logging.getLogger(__name__)

API_BASE = "https://developer.toutiao.com"
CREATE_ORDER_URL = f"{API_BASE}/api/apps/ecpay/v1/create_order"
QUERY_ORDER_URL = f"{API_BASE}/api/apps/ecpay/v1/query_order"
CREATE_REFUND_URL = f"{API_BASE}/api/apps/ecpay/v1/create_refund"


def _sign(params: dict, salt: str) -> str:
    """HMAC-SHA256 签名

    1. 按 key 字典序排列参数（排除 sign 字段）
    2. 拼接 key=value& 格式字符串
    3. 追加 &key={salt}
    4. 计算 HMAC-SHA256 并转小写十六进制
    """
    filtered = {k: v for k, v in params.items() if k != "sign" and v is not None and v != ""}
    sorted_keys = sorted(filtered.keys())
    query_string = "&".join(f"{k}={filtered[k]}" for k in sorted_keys)
    string_to_sign = f"{query_string}&key={salt}"
    return hmac.new(salt.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


class DouyinPay(BasePayment):
    """抖音支付插件"""

    def __init__(self, channel: dict):
        super().__init__(channel)
        self.app_id = channel.get("appid", "")
        self.salt = channel.get("appsecret", "")
        self.merchant_id = channel.get("merchant_id", "")

    def _get_notify_url(self) -> str:
        """获取异步回调地址"""
        notify_url = self.channel.get("notify_url", "")
        if notify_url:
            return notify_url
        # 兜底：使用系统配置中的域名拼接
        return ""

    def submit(self, ctx: PaymentContext) -> PaymentResult:
        """提交支付订单

        根据 channel 配置的 select 字段决定支付方式：
          1 — 小程序支付（返回 json 数据供前端拉起支付）
          2 — H5支付（返回跳转 URL）
        """
        order = ctx.order
        trade_no = order.get("trade_no", "")
        money = order.get("money", 0)

        # 金额转换为分
        total_amount = int(round(float(money) * 100))
        if total_amount <= 0:
            raise ValueError(f"Invalid payment amount: {money}")

        order_name = ctx.ordername or order.get("ordername", "Order")
        subject = order_name[:42]  # 抖音限制 subject 最长42个字符

        body = {
            "app_id": self.app_id,
            "out_order_no": trade_no,
            "total_amount": total_amount,
            "subject": subject,
            "body": order_name,
            "valid_time": 1800,
            "notify_url": self._get_notify_url(),
            "cp_extra": json.dumps({}),
        }

        # 计算签名
        sign = _sign(body, self.salt)
        body["sign"] = sign

        # 确定支付方式
        pay_mode = str(self.channel.get("select", "1"))

        try:
            resp = httpx.post(CREATE_ORDER_URL, json=body, timeout=15)
            resp_data = resp.json()
        except httpx.RequestError as e:
            logger.error("DouyinPay create_order request failed: %s", e)
            raise RuntimeError(f"DouyinPay request failed: {e}") from e
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("DouyinPay create_order response parse failed: %s", e)
            raise RuntimeError(f"DouyinPay response parse failed: {e}") from e

        logger.info("DouyinPay create_order response: %s", resp_data)

        err_no = resp_data.get("err_no", -1)
        if err_no != 0:
            err_tips = resp_data.get("err_tips", "Unknown error")
            logger.error("DouyinPay create_order error: err_no=%s, err_tips=%s", err_no, err_tips)
            raise RuntimeError(f"DouyinPay error [{err_no}]: {err_tips}")

        data = resp_data.get("data", {})

        if pay_mode == "2":
            # H5 支付 — 返回跳转 URL
            payment_url = data.get("url", "")
            if not payment_url:
                raise RuntimeError("DouyinPay H5 payment URL is empty")
            return PaymentResult(
                pay_type="jump",
                pay_info=payment_url,
                expires_in=1800,
            )
        else:
            # 小程序支付 — 返回 JSON 数据（order_id + order_token）
            order_id = data.get("order_id", "")
            order_token = data.get("order_token", "")
            if not order_id or not order_token:
                raise RuntimeError("DouyinPay mini-program response missing order_id or order_token")
            return PaymentResult(
                pay_type="jsapi",
                pay_info=json.dumps({"order_id": order_id, "order_token": order_token}),
                expires_in=1800,
                extra={"order_id": order_id, "order_token": order_token},
            )

    def notify(self, params: dict, body: bytes = b"") -> Optional[dict]:
        """处理异步回调通知

        验证签名后解析支付结果，返回标准格式:
          {trade_no, api_trade_no, money, buyer}
        """
        if not body:
            logger.warning("DouyinPay notify: empty body")
            return None

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error("DouyinPay notify body parse failed: %s", e)
            return None

        logger.info("DouyinPay notify data: %s", data)

        # 验证签名
        received_sign = data.get("sign", "")
        if not received_sign:
            logger.warning("DouyinPay notify: missing sign")
            return None

        expected_sign = _sign(data, self.salt)
        if not hmac.compare_digest(expected_sign, received_sign):
            logger.warning("DouyinPay notify: signature mismatch (expected=%s, got=%s)", expected_sign, received_sign)
            return None

        # 检查支付状态
        status = data.get("status", "")
        if status != "SUCCESS":
            logger.info("DouyinPay notify: status=%s (not SUCCESS, ignoring)", status)
            return None

        out_order_no = data.get("out_order_no", "")
        order_id = data.get("order_id", "")
        total_amount = data.get("total_amount", 0)

        # 金额从分转为元
        money = float(total_amount) / 100.0

        return {
            "trade_no": out_order_no,
            "api_trade_no": order_id,
            "money": money,
            "buyer": data.get("seller_id", ""),
        }

    def return_handler(self, params: dict) -> Optional[dict]:
        """处理同步返回（抖音一般不使用同步回调）"""
        return None

    def refund(self, order: dict, refund_no: str, money: float) -> dict:
        """发起退款

        退款金额以分为单位发送到抖音 API
        """
        refund_amount = int(round(float(money) * 100))
        total_amount = int(round(float(order.get("money", 0)) * 100))

        body = {
            "app_id": self.app_id,
            "out_order_no": order.get("trade_no", ""),
            "out_refund_no": refund_no,
            "refund_amount": refund_amount,
            "reason": "Refund",
        }

        sign = _sign(body, self.salt)
        body["sign"] = sign

        try:
            resp = httpx.post(CREATE_REFUND_URL, json=body, timeout=15)
            resp_data = resp.json()
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            logger.error("DouyinPay refund request failed: %s", e)
            return {"status": "error", "message": str(e)}

        logger.info("DouyinPay refund response: %s", resp_data)

        err_no = resp_data.get("err_no", -1)
        if err_no != 0:
            err_tips = resp_data.get("err_tips", "Unknown error")
            logger.error("DouyinPay refund error: err_no=%s, err_tips=%s", err_no, err_tips)
            return {"status": "error", "message": f"[{err_no}] {err_tips}"}

        return {
            "status": "success",
            "refund_no": refund_no,
            "api_refund_no": resp_data.get("data", {}).get("refund_id", ""),
        }

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True
