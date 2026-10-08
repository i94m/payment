"""通道凭证加解密 — Fernet（密钥派生自 APP_KEY）。

channel.config 落库前必须 encrypt_config()，读取后 decrypt_config()。
admin API 凭证明文返回，支持直接编辑。
"""
import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import config

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        digest = hashlib.sha256(config.APP_SECRET_KEY.encode("utf-8")).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encrypt_config(data: dict[str, Any]) -> str:
    """加密凭证 dict → 密文字符串（落库）。"""
    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_config(ciphertext: str | None) -> dict[str, Any]:
    """解密密文 → 凭证 dict。兼容明文 JSON（迁移期容错）。"""
    if not ciphertext:
        return {}
    try:
        plaintext = _get_fernet().decrypt(ciphertext.encode("ascii"))
        return json.loads(plaintext.decode("utf-8"))
    except (InvalidToken, UnicodeEncodeError):
        # 兼容历史明文 JSON
        try:
            data = json.loads(ciphertext)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    except json.JSONDecodeError:
        return {}
