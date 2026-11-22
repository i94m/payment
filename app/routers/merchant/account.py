"""商户端账户/安全：资料、改密、API 密钥（MD5 与 RSA2 二选一，由 RSA2 开关切换）。"""
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import col, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import MerchantUser, hash_password, revoke_all_tokens, verify_password
from app.lib.sign import derive_public_key, get_sign_content, make_md5_sign, pem_to_flat
from app.models import ChannelCode, Merchant
from app.responses import err, suc
from app.services.apikey import ensure_api_secret, set_api_secret
from app.services.settings import get_all_settings

router = APIRouter()


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/changePassword")
async def change_password(body: ChangePasswordRequest, auth: MerchantUser, session: SessionDep,
                          cache: CacheDep):
    """商户自助改密。

    P1-10：改密后吊销该商户其它所有登录令牌，只保留当前会话
    （被盗令牌不能靠改密继续存活，当前操作者也不会被自己踢下线）。
    """
    m = auth.user
    if not verify_password(body.old_password, m.password):
        return err(-1, "原密码错误")
    if len(body.new_password) < 6:
        return err(-1, "新密码至少 6 位")
    m.password = hash_password(body.new_password)
    session.add(m)
    await revoke_all_tokens("merchant", m.id, session, cache=cache, except_jti=auth.jti)
    await session.commit()
    return suc("密码修改成功")


@router.get("/apikey")
async def get_apikey(auth: MerchantUser, session: SessionDep, cache: CacheDep):
    """查看 API 密钥信息。

    - api_secret：MD5 签名密钥（未开启 RSA2 时生效；本人可反复查看/复制/重置）
    - rsa2_enable / public_key：RSA2 是否开启及商户公钥（开启后 MD5 失效）
    - platform_public_key：平台公钥（验 RSA2 通知签名用）
    - 对接信息（面板「对接信息」一键复制用）：username /
      channel_code（启用中的测试编码，`is_test` 显式标记；没有则为 null）
    - 下单/查询/文档地址的域名由前端取 `window.location.origin` 拼接，
      后端不再下发 site_url（站点地址配置不等于用户实际访问的域名）
    """
    m = auth.user
    secret = await ensure_api_secret(session, m)
    settings = await get_all_settings(session, cache)
    try:
        platform_public_key = derive_public_key(str(settings.get("platform_rsa_private_key") or ""))
    except ValueError:
        platform_public_key = ""
    # 对接信息只展示被标成「测试编码」的那条：标记挂在编码上（is_test），
    # 不再按名称/备注猜用途。允许多条标记时按 id 升序取第一条启用中的，行为确定；
    # 没有测试编码就整行不展示（不回退到正式编码）。
    picked = (await session.exec(
        select(ChannelCode)
        .where(col(ChannelCode.status) == 1, col(ChannelCode.is_test).is_(True))
        .order_by(col(ChannelCode.id))
        .limit(1)
    )).first()
    return suc({
        "pid": m.pid,
        "username": m.username,
        "api_secret": secret,
        "public_key": pem_to_flat(m.public_key or ""),
        "rsa2_enable": bool(m.rsa2_enable),
        "platform_public_key": platform_public_key,
        "channel_code": (
            {"code": picked.code, "name": picked.name or "", "is_test": True} if picked else None
        ),
    })


@router.post("/apikey/secret")
async def regenerate_api_secret(auth: MerchantUser, session: SessionDep):
    """重置 MD5 签名密钥（旧密钥立即失效，接入方需同步更换）。"""
    m = auth.user
    secret = set_api_secret(m)
    session.add(m)
    await session.commit()
    return suc({"api_secret": secret})


class Rsa2SwitchRequest(BaseModel):
    enable: bool


@router.post("/rsa2")
async def toggle_rsa2(body: Rsa2SwitchRequest, auth: MerchantUser, session: SessionDep):
    """开启/关闭 RSA2 签名（开启需先配置公钥；开启后 MD5 签名立即失效）。"""
    m = auth.user
    if body.enable and not (m.public_key or "").strip():
        return err(-1, "请先配置RSA公钥后再开启RSA2签名")
    m.rsa2_enable = body.enable
    session.add(m)
    await session.commit()
    return suc({"rsa2_enable": bool(m.rsa2_enable)})


class SignDebugRequest(BaseModel):
    params: dict


@router.post("/sign-debug")
async def sign_debug(body: SignDebugRequest, auth: MerchantUser, session: SessionDep):
    """签名自检：回显签名原文与当前生效算法的期望签名。

    同一时间只有一种算法生效，只返回该算法的结果，避免拿已停用算法的签名排查。
    RSA2 的签名只能由商户私钥生成（平台不保存私钥），自检只返回拼串；
    MD5 可直接给出期望值，便于一次定位「拼串错」还是「密钥错」。
    """
    m = auth.user
    params = {k: v for k, v in (body.params or {}).items() if k not in ("sign", "sign_type")}
    content = get_sign_content(params)
    data: dict = {
        "sign_content": content,
        "active_sign_type": "RSA2" if m.rsa2_enable else "MD5",
        "algorithms": {},
    }
    if m.rsa2_enable:
        data["algorithms"]["RSA2"] = {"note": "请用你的商户私钥对签名原文做 SHA256WithRSA 签名（Base64）"}
    else:
        secret = await ensure_api_secret(session, m)
        data["algorithms"]["MD5"] = {"sign": make_md5_sign(params, secret)}
    return suc(data)


@router.post("/rsa/generate")
async def generate_rsa_pair(auth: MerchantUser, session: SessionDep):
    """一键生成 RSA 密钥对并保存公钥。

    平台生成 RSA-2048 密钥对：公钥直接落库（API 立即可用），
    私钥仅此一次返回给商户保存（平台不保存）。
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
