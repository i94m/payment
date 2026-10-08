"""腾讯云短信插件 — 使用 SMS API v2021-01-11 (TC3-HMAC-SHA256 签名)"""
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

import httpx

from app.services.plugin_interfaces import BaseSms

logger = logging.getLogger(__name__)

SMS_HOST = "sms.tencentcloudapi.com"
SMS_SERVICE = "sms"
SMS_API_URL = f"https://{SMS_HOST}"
API_VERSION = "2021-01-11"
ACTION = "SendSms"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


class QcloudSms(BaseSms):
    """腾讯云短信插件"""

    def __init__(self, config: dict):
        self.secret_id = config.get("secret_id", "")
        self.secret_key = config.get("secret_key", "")
        self.app_id = config.get("app_id", "")
        self.sign_name = config.get("sign_name", "")

    def _build_authorization(self, payload: str, timestamp: int, date: str) -> str:
        """构建 TC3-HMAC-SHA256 Authorization 头"""
        content_type = "application/json; charset=utf-8"
        canonical_headers = f"content-type:{content_type}\nhost:{SMS_HOST}\nx-tc-action:{ACTION.lower()}\n"
        signed_headers = "content-type;host;x-tc-action"

        canonical_request = (
            f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{_sha256_hex(payload)}"
        )

        algorithm = "TC3-HMAC-SHA256"
        credential_scope = f"{date}/{SMS_SERVICE}/tc3_request"
        string_to_sign = (
            f"{algorithm}\n{timestamp}\n{credential_scope}\n{_sha256_hex(canonical_request)}"
        )

        secret_date = _hmac_sha256(("TC3" + self.secret_key).encode("utf-8"), date)
        secret_service = _hmac_sha256(secret_date, SMS_SERVICE)
        secret_signing = _hmac_sha256(secret_service, "tc3_request")

        signature = hmac.new(
            secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        return (
            f"{algorithm} Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

    def send(self, phone: str, tpl_code: str, tpl_param: dict) -> bool:
        """发送短信

        Args:
            phone: 手机号码
            tpl_code: 模板 ID
            tpl_param: 模板参数，如 {"code": "123456"}

        Returns:
            True 发送成功, False 发送失败
        """
        if not phone:
            logger.error("QcloudSms: phone number is empty")
            return False

        template_param_set = []
        if tpl_param and isinstance(tpl_param, dict):
            template_param_set = [str(v) for v in tpl_param.values()]

        now = datetime.now(timezone.utc)
        timestamp = int(now.timestamp())
        date = now.strftime("%Y-%m-%d")

        body = {
            "SmsSdkAppId": self.app_id,
            "SignName": self.sign_name,
            "TemplateId": str(tpl_code),
            "TemplateParamSet": template_param_set,
            "PhoneNumberSet": [f"+86{phone}" if not phone.startswith("+") else phone],
        }
        payload = json.dumps(body, ensure_ascii=False)

        authorization = self._build_authorization(payload, timestamp, date)

        headers = {
            "Authorization": authorization,
            "Content-Type": "application/json; charset=utf-8",
            "Host": SMS_HOST,
            "X-TC-Action": ACTION,
            "X-TC-Version": API_VERSION,
            "X-TC-Region": "ap-guangzhou",
            "X-TC-Timestamp": str(timestamp),
        }

        try:
            resp = httpx.post(SMS_API_URL, headers=headers, content=payload, timeout=15)
            resp_data = resp.json()
        except Exception as e:
            logger.error("QcloudSms request failed: %s", e)
            return False

        response = resp_data.get("Response", {})
        error_code = response.get("Error", {}).get("Code", "")

        if error_code:
            logger.error(
                "QcloudSms send failed: code=%s, message=%s",
                error_code, response.get("Error", {}).get("Message", ""),
            )
            return False

        send_status_set = response.get("SendStatusSet", [])
        if send_status_set and send_status_set[0].get("Code") == "Ok":
            logger.info("QcloudSms sent successfully: phone=%s", phone)
            return True

        logger.error("QcloudSms send failed: phone=%s, response=%s", phone, resp_data)
        return False
