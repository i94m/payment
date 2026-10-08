"""阿里云邮件插件 — 使用 DirectMail API (HMAC-SHA1 签名, POST)"""
import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import uuid

import httpx

from app.services.plugin_interfaces import BaseMail

logger = logging.getLogger(__name__)

DM_API_URL = "https://dm.aliyuncs.com/"
API_VERSION = "2015-11-23"
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


class AliyunMail(BaseMail):
    """阿里云邮件插件"""

    def __init__(self, config: dict):
        self.accesskey_id = config.get("accesskey_id", "")
        self.accesskey_secret = config.get("accesskey_secret", "")
        self.account_name = config.get("account_name", "")
        self.region = config.get("region", "cn-hangzhou")

    def send(self, to: str, subject: str, body: str) -> bool:
        """发送邮件

        Args:
            to: 收件人地址
            subject: 邮件主题
            body: 邮件正文（HTML）

        Returns:
            True 发送成功, False 发送失败
        """
        if not to or not subject:
            logger.error("AliyunMail: recipient or subject is empty")
            return False

        params = {
            "Action": "SingleSendMail",
            "Version": API_VERSION,
            "Format": FORMAT,
            "AccessKeyId": self.accesskey_id,
            "SignatureMethod": SIGNATURE_METHOD,
            "SignatureVersion": SIGNATURE_VERSION,
            "SignatureNonce": str(uuid.uuid4()),
            "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "RegionId": self.region,
            "AccountName": self.account_name,
            "AddressType": "1",
            "ReplyToAddress": "false",
            "ToAddress": to,
            "Subject": subject,
            "HtmlBody": body,
        }

        signature = _compute_signature(params, self.accesskey_secret)
        params["Signature"] = signature

        try:
            resp = httpx.post(DM_API_URL, data=params, timeout=15)
            resp_data = resp.json()
        except Exception as e:
            logger.error("AliyunMail request failed: %s", e)
            return False

        code = resp_data.get("Code", "")
        if not code or code == "":
            logger.info("AliyunMail sent successfully: to=%s", to)
            return True

        logger.error(
            "AliyunMail send failed: to=%s, code=%s, message=%s",
            to, code, resp_data.get("Message", ""),
        )
        return False
