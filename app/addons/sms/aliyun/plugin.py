"""阿里云短信插件 — 使用 dysmsapi SendSms 接口 (HMAC-SHA1 签名, POST)"""
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import uuid

import httpx

from app.services.plugin_interfaces import BaseSms

logger = logging.getLogger(__name__)

SMS_API_URL = "https://dysmsapi.aliyuncs.com/"
API_VERSION = "2017-05-25"
SIGNATURE_METHOD = "HMAC-SHA1"
SIGNATURE_VERSION = "1.0"
FORMAT = "JSON"


def _percent_encode(s: str) -> str:
    """URL 编码（阿里云规范）"""
    return urllib.parse.quote(s, safe="~").replace("+", "%20").replace("*", "%2A")


def _compute_signature(params: dict, access_key_secret: str) -> str:
    """计算阿里云 HMAC-SHA1 签名"""
    sorted_params = sorted(params.items())
    query_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(str(v))}" for k, v in sorted_params
    )
    string_to_sign = f"POST&%2F&{_percent_encode(query_string)}"
    key = f"{access_key_secret}&"
    signature = hmac.new(
        key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(signature).decode("utf-8")


class AliyunSms(BaseSms):
    """阿里云短信插件"""

    def __init__(self, config: dict):
        self.accesskey_id = config.get("accesskey_id", "")
        self.accesskey_secret = config.get("accesskey_secret", "")
        self.sign_name = config.get("sign_name", "")
        self.region = config.get("region", "cn-hangzhou")

    def send(self, phone: str, tpl_code: str, tpl_param: dict) -> bool:
        """发送短信

        Args:
            phone: 手机号码
            tpl_code: 短信模板 CODE
            tpl_param: 模板参数字典，如 {"code": "123456"}

        Returns:
            True 发送成功, False 发送失败
        """
        if not phone:
            logger.error("AliyunSms: phone number is empty")
            return False

        params = {
            "Action": "SendSms",
            "Version": API_VERSION,
            "Format": FORMAT,
            "AccessKeyId": self.accesskey_id,
            "SignatureMethod": SIGNATURE_METHOD,
            "SignatureVersion": SIGNATURE_VERSION,
            "SignatureNonce": str(uuid.uuid4()),
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "RegionId": self.region,
            "PhoneNumbers": phone,
            "SignName": self.sign_name,
            "TemplateCode": tpl_code,
        }

        if tpl_param:
            params["TemplateParam"] = json.dumps(tpl_param, ensure_ascii=False)

        signature = _compute_signature(params, self.accesskey_secret)
        params["Signature"] = signature

        try:
            resp = httpx.post(SMS_API_URL, data=params, timeout=15)
            resp_data = resp.json()
        except Exception as e:
            logger.error("AliyunSms request failed: %s", e)
            return False

        code = resp_data.get("Code", "")
        if code == "OK":
            logger.info("AliyunSms sent successfully: phone=%s", phone)
            return True

        logger.error(
            "AliyunSms send failed: phone=%s, code=%s, message=%s",
            phone, code, resp_data.get("Message", ""),
        )
        return False
