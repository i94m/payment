"""不透明链接令牌 — 收银台 /cashier/ 链接不暴露业务号。

收银台 /cashier/{token}：Fernet 加密的随机外观串（约 100 字符 URL-safe），
无状态可逆，无需落库；轮换 APP_SECRET_KEY 即全量失效（派生密钥与
通道凭证加密相互独立）。

收款页 /p/{code}：随机 10 字符短码 + pay_tokens 表映射（见
services/paytoken.py），链接形态见 paypage_url。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import config

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """链接令牌专用派生密钥（与 crypto.py 的通道凭证密钥隔离，用途域分隔）。"""
    global _fernet
    if _fernet is None:
        digest = hashlib.sha256(
            config.APP_SECRET_KEY.encode("utf-8") + b"|link-token").digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encode_link_token(value: str) -> str:
    """业务号 → 不透明令牌（URL-safe，可直接进路径）。"""
    return _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decode_link_token(token: str) -> str | None:
    """令牌 → 业务号；无效/篡改返回 None。"""
    if not token or len(token) > 512:
        return None
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None


def cashier_url(site: str, trade_no: str) -> str:
    """收银台不透明链接：{site}/cashier/{token}。"""
    return f"{site.rstrip('/')}/cashier/{encode_link_token(trade_no)}"


def paypage_url(code: str) -> str:
    """收款页短链：/p/{code}（code 由 services/paytoken.py 按商户/代理映射）。"""
    return f"/p/{code}"
