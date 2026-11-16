"""不透明链接令牌 — 收银台 /cashier/ 链接不暴露业务号。

收银台 /cashier/{token}：Fernet 加密的随机外观串（约 100 字符 URL-safe），
无状态可逆，无需落库；轮换 APP_SECRET_KEY 即全量失效（派生密钥与
通道凭证加密相互独立）。

令牌内置签发时间戳并校验 TTL（P1-15）：此前链接永久有效，一旦出现在
访问日志/分享里就是长期可用的付款入口。默认 24 小时（收银台只服务即时支付），
`CASHIER_LINK_TTL_MINUTES` 可在 .env 调整。

收款页 /p/{code}：随机 10 字符短码 + pay_tokens 表映射（见
services/paytoken.py），链接形态见 paypage_url。
"""
import base64
import hashlib
import time

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import config

_fernet: Fernet | None = None

# 收银台链接有效期（分钟）：0 或负数视为不限制（仅排障用，不建议生产）
DEFAULT_LINK_TTL_MINUTES = 24 * 60


def link_ttl_seconds() -> int:
    minutes = int(getattr(config, "CASHIER_LINK_TTL_MINUTES", DEFAULT_LINK_TTL_MINUTES) or 0)
    return max(minutes, 0) * 60


def _get_fernet() -> Fernet:
    """链接令牌专用派生密钥（与 crypto.py 的通道凭证密钥隔离，用途域分隔）。"""
    global _fernet
    if _fernet is None:
        digest = hashlib.sha256(
            config.APP_SECRET_KEY.encode("utf-8") + b"|link-token").digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encode_link_token(value: str) -> str:
    """业务号 → 不透明令牌（URL-safe，可直接进路径；载荷含签发时间）。"""
    payload = f"{value}|{int(time.time())}"
    return _get_fernet().encrypt(payload.encode("utf-8")).decode("ascii")


def decode_link_token(token: str) -> str | None:
    """令牌 → 业务号；无效/篡改/过期返回 None。"""
    if not token or len(token) > 512:
        return None
    try:
        payload = _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None
    value, _, issued = payload.rpartition("|")
    if not value or not issued.isdigit():
        # 无时间戳的历史令牌：按已过期处理（收银台链接本就是短生命周期）
        return None
    ttl = link_ttl_seconds()
    if ttl > 0 and (time.time() - int(issued)) > ttl:
        return None
    return value


def cashier_url(site: str, trade_no: str) -> str:
    """收银台不透明链接：{site}/cashier/{token}。"""
    return f"{site.rstrip('/')}/cashier/{encode_link_token(trade_no)}"


def paypage_url(code: str) -> str:
    """收款页短链：/p/{code}（code 由 services/paytoken.py 按商户/代理映射）。"""
    return f"/p/{code}"
