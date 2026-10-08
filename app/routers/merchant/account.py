"""商户端账户/安全：资料、改密、RSA 密钥管理。"""
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import CacheDep, SessionDep
from app.core.security import MerchantUser, hash_password, verify_password
from app.lib.sign import derive_public_key, pem_to_flat
from app.models import Merchant
from app.responses import err, suc
from app.services.settings import get_all_settings

router = APIRouter()


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/changePassword")
async def change_password(body: ChangePasswordRequest, auth: MerchantUser, session: SessionDep):
    m = auth.user
    if not verify_password(body.old_password, m.password):
        return err(-1, "原密码错误")
    if len(body.new_password) < 6:
        return err(-1, "新密码至少 6 位")
    m.password = hash_password(body.new_password)
    session.add(m)
    await session.commit()
    return suc("密码修改成功")


@router.get("/apikey")
async def get_apikey(auth: MerchantUser, session: SessionDep, cache: CacheDep):
    """查看 API 密钥信息。platform_public_key 由平台私钥派生（未配置/无效为空串），
    商户用于验证平台 Webhook/回跳签名。"""
    m = auth.user
    settings = await get_all_settings(session, cache)
    try:
        platform_public_key = derive_public_key(str(settings.get("platform_rsa_private_key") or ""))
    except ValueError:
        platform_public_key = ""
    return suc({
        "pid": m.pid,
        "public_key": pem_to_flat(m.public_key or ""),
        "platform_public_key": platform_public_key,
    })


class PublicKeyRequest(BaseModel):
    public_key: str


@router.post("/publickey")
async def set_publickey(body: PublicKeyRequest, auth: MerchantUser, session: SessionDep):
    """保存商户 RSA 公钥（API 调用验签用）。传空即清除公钥。"""
    m = auth.user
    if body.public_key.strip():
        m.public_key = body.public_key.strip()
    else:
        m.public_key = None
    session.add(m)
    await session.commit()
    return suc({"public_key": pem_to_flat(m.public_key or "")})


@router.post("/rsa/generate")
async def generate_rsa_pair(auth: MerchantUser, session: SessionDep):
    """一键生成 RSA 密钥对并自动保存公钥。

    平台生成 RSA-2048 密钥对：公钥直接落库（API 立即可用），
    私钥仅此一次返回给商户下载保存（平台不保存）。
    """
    m = auth.user
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    m.public_key = pem_to_flat(public_pem)
    session.add(m)
    await session.commit()
    return suc({
        "public_key": pem_to_flat(public_pem),
        "private_key": pem_to_flat(private_pem),
    })


class ProfileRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    qq: Optional[str] = None
    url: Optional[str] = None
    default_notify_url: Optional[str] = None
    default_return_url: Optional[str] = None
    settle_account: Optional[str] = None
    settle_name: Optional[str] = None
    settle_type: Optional[int] = None  # 1支付宝 2微信 3QQ 4银行卡
    notify_order: Optional[bool] = None
    notify_settle: Optional[bool] = None
    notify_login: Optional[bool] = None
    notify_lowbalance: Optional[bool] = None


@router.put("/profile")
async def update_profile(body: ProfileRequest, auth: MerchantUser, session: SessionDep):
    """资料修改（部分更新：仅写请求显式携带的字段，未携带字段保持原值）。

    「修改资料」页只提交结算方式/收款账号/真实姓名；
    结算页快速切换结算方式也仅提交 settle_type。
    """
    m = auth.user
    data = body.model_dump(exclude_unset=True)
    for key in ("email", "phone", "qq", "url", "default_notify_url", "default_return_url",
                "settle_account", "settle_name"):
        if key in data:
            setattr(m, key, data[key] or None)
    if data.get("settle_type") in (1, 2, 3, 4):
        m.settle_type = data["settle_type"]
    for key in ("notify_order", "notify_settle", "notify_login", "notify_lowbalance"):
        if key in data:
            setattr(m, key, data[key])
    session.add(m)
    await session.commit()
    return suc("保存成功")
