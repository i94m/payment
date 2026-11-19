"""商户开放 API 密钥（MD5 对称密钥）管理。

MD5 是开放 API 的默认签名方式：商户创建即生成一把 32 位小写 hex 密钥，
面板可反复查看/复制/重置。落库走 services/crypto.py 的 Fernet 加密
（只存密文，读取时解密），与通道凭证同口径。
"""
import secrets

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant
from app.services.crypto import decrypt_config, encrypt_config

# 密文内部键名（与通道凭证共用 encrypt_config 的 dict 结构）
_SECRET_KEY = "k"


def new_api_secret() -> str:
    """生成新密钥：32 位小写 hex（与易支付 appkey 同形）。"""
    return secrets.token_hex(16)


def get_api_secret(merchant: Merchant) -> str:
    """读取商户 MD5 密钥明文；未配置返回空串。"""
    return str(decrypt_config(merchant.api_secret).get(_SECRET_KEY) or "")


def set_api_secret(merchant: Merchant, secret: str | None = None) -> str:
    """写入（重新生成）密钥，返回明文供本次展示。"""
    raw = secret or new_api_secret()
    merchant.api_secret = encrypt_config({_SECRET_KEY: raw})
    return raw


async def ensure_api_secret(session: AsyncSession, merchant: Merchant) -> str:
    """保证商户有密钥（历史商户/老数据兜底），返回明文。"""
    raw = get_api_secret(merchant)
    if raw:
        return raw
    raw = set_api_secret(merchant)
    session.add(merchant)
    await session.commit()
    return raw
