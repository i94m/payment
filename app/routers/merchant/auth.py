"""商户端认证（JWT guard=merchant）：登录（验证码）/SSO/登出/找回密码。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import select

from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.core.security import (
    MerchantUser,
    dump_user_tokens,
    hash_password,
    issue_token,
    revoke_all_tokens,
    revoke_token,
    revoke_user_token_by_id,
    single_session_default,
    verify_password,
)
from app.models import Merchant, MerchantLoginLog
from app.responses import err, suc
from app.services.apikey import set_api_secret
from app.services.merchant import generate_merchant_pid

router = APIRouter()

# 找回密码验证码：连续错误次数上限与计数窗口（P1-11）
RESET_MAX_ATTEMPTS = 5
RESET_ATTEMPT_WINDOW = 15 * 60


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_key: str = ""
    captcha_code: str = ""


class SsoLoginRequest(BaseModel):
    """管理员 SSO 登录：凭 admin/merchant/{id}/sso 下发的一次性 token 换取正式 JWT。"""
    token: str


async def _record_login(session, merchant: Merchant, request: Request, ip: str | None) -> None:
    """登录日志 + 按订阅发站内消息/邮件。失败不阻断登录。"""
    from app.services.message import send_merchant_message
    try:
        session.add(MerchantLoginLog(
            merchant_id=merchant.id,
            ip=(ip or "")[:45],
            user_agent=(request.headers.get("user-agent", "") or "")[:500],
        ))
        await send_merchant_message(
            session, merchant, title="登录提醒",
            content=f"账号于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 登录（IP {ip or '未知'}）。",
            biz_type="system", subscribe_flag="notify_login",
        )
        merchant.last_login_at = datetime.now()
        session.add(merchant)
        await session.commit()
    except Exception:
        await session.rollback()


async def _issue_login(
    session,
    cache,
    merchant: Merchant,
    request: Request,
    name: str = "",
    single_session: bool | None = None,
):
    ip = request.client.host if request.client else None
    # 默认跟随 .env 的 LOGIN_MULTI_SESSION；SSO 代登录显式传 False（不撤销商户已有会话）
    if single_session is None:
        single_session = single_session_default()
    jwt_token = await issue_token(
        guard="merchant",
        user_id=merchant.id,
        session=session,
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
        single_session=single_session,
        name=name or None,
        cache=cache,
    )
    await _record_login(session, merchant, request, ip)
    return jwt_token


@router.post("/sso")
async def sso_login(body: SsoLoginRequest, request: Request, session: SessionDep, cache: CacheDep):
    token = (body.token or "").strip()
    if not token:
        return err(-1, "登录凭证无效")
    try:
        merchant_id = int(await cache.get(f"sso:merchant:{token}") or 0)
    except (TypeError, ValueError):
        merchant_id = 0
    if merchant_id <= 0:
        return err(-1, "登录凭证已失效，请让管理员重新发起")
    # 一次性：立即作废
    try:
        await cache.delete(f"sso:merchant:{token}")
    except Exception:
        pass

    m = await session.get(Merchant, merchant_id)
    if not m:
        return err(-1, "商户不存在")
    if m.status != 1:
        return err(-1, "商户已被禁用，请联系管理员")

    # SSO 是管理员代登录，不应撤销商户已有登录会话。
    jwt_token = await _issue_login(
        session, cache, m, request, name="sso", single_session=False
    )
    return suc({
        "token_type": jwt_token.token_type,
        "access_token": jwt_token.access_token,
        "expires_in": jwt_token.expires_in,
    })


@router.post("/login")
async def login(body: LoginRequest, request: Request, session: SessionDep, cache: CacheDep):
    from app.lib.captcha import verify_captcha_code
    captcha_err = await verify_captcha_code(cache, body.captcha_key, body.captcha_code)
    if captcha_err:
        return err(-1, captcha_err)

    m = (await session.exec(
        select(Merchant).where(Merchant.username == body.username)
    )).first()
    if not m or not verify_password(body.password, m.password):
        return err(-1, "用户名或密码错误")
    if m.status != 1:
        return err(-1, "商户已被禁用，请联系管理员")

    token = await _issue_login(session, cache, m, request)
    return suc({
        "token_type": token.token_type,
        "access_token": token.access_token,
        "expires_in": token.expires_in,
    })


@router.post("/logout")
async def logout(auth: MerchantUser, session: SessionDep, cache: CacheDep):
    if auth.jti:
        await revoke_token(auth.jti, session, cache)
    return suc("success")


@router.get("/profile")
async def profile(auth: MerchantUser, session: SessionDep):
    m = auth.user
    # 商户端单一角色：permissions=['*'] 使模板权限守卫全量放行。
    # 结算/回调默认地址一并下发：安全设置页需要回显，
    # 否则表单空值提交会把已存的 default_notify_url 等清掉。
    return suc({
        "id": m.id,
        "pid": m.pid,
        "username": m.username,
        "name": m.username,
        "is_super_admin": True,
        "permissions": ["*"],
        "email": m.email or "",
        "phone": m.phone or "",
        "qq": m.qq or "",
        "url": m.url or "",
        "balance": str(m.balance),
        "status": m.status,
        "default_notify_url": m.default_notify_url or "",
        "default_return_url": m.default_return_url or "",
        "settle_account": m.settle_account or "",
        "settle_name": m.settle_name or "",
        "settle_type": m.settle_type or 1,
        "rate": str(m.rate) if m.rate is not None else "",
        "invite_code": m.invite_code or "",
        "notify_order": bool(m.notify_order),
        "notify_settle": bool(m.notify_settle),
        "notify_login": bool(m.notify_login),
        "notify_lowbalance": bool(m.notify_lowbalance),
        "remain_money": str(m.remain_money),
        "auto_settle": bool(m.auto_settle),
    })


@router.get("/tokens")
async def tokens(auth: MerchantUser, session: SessionDep):
    return suc(await dump_user_tokens("merchant", auth.user.id, auth.jti, session))


@router.delete("/tokens/{token_id}")
async def delete_token(token_id: int, auth: MerchantUser, session: SessionDep):
    ok, msg = await revoke_user_token_by_id(token_id, "merchant", auth.user.id, auth.jti, session)
    if not ok:
        return err(-1, msg)
    return suc("success")


# ============================================================
# 商户自助注册（merchant_reg_open 控制；merchant_reg_review=待审核）
# ============================================================

class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""
    phone: str = ""
    invite_code: str = ""
    captcha_key: str = ""
    captcha_code: str = ""


def _valid_password(password: str, username: str) -> str:
    """密码规则：≥6 位、非纯数字、不得与用户名相同。"""
    if len(password or "") < 6:
        return "密码至少 6 位"
    if password.isdigit():
        return "密码不能为纯数字"
    if password == username:
        return "密码不能与用户名相同"
    return ""


@router.post("/register")
async def register(body: RegisterRequest, request: Request, session: SessionDep, cache: CacheDep):
    from app.lib.captcha import verify_captcha_code
    from app.services.settings import get_setting

    captcha_err = await verify_captcha_code(cache, body.captcha_key, body.captcha_code)
    if captcha_err:
        return err(-1, captcha_err)

    if not bool(await get_setting(session, "merchant_reg_open", False)):
        return err(-1, "平台未开放商户注册，请联系管理员")

    username = (body.username or "").strip()
    email = (body.email or "").strip().lower()
    if not username or len(username) > 32:
        return err(-1, "用户名不能为空且不超过 32 字符")
    if email and ("@" not in email or len(email) > 128):
        return err(-1, "邮箱格式错误")

    pwd_err = _valid_password(body.password, username)
    if pwd_err:
        return err(-1, pwd_err)

    existing = (await session.exec(
        select(Merchant).where(Merchant.username == username)
    )).first()
    if existing:
        return err(-1, "用户名已存在")

    # 邀请码绑定：无效邀请码不阻断注册
    invited_by = 0
    code = (body.invite_code or "").strip()
    if code:
        inviter = (await session.exec(
            select(Merchant).where(Merchant.invite_code == code, Merchant.status == 1)
        )).first()
        if inviter:
            invited_by = inviter.id

    import secrets
    from decimal import Decimal
    review = bool(await get_setting(session, "merchant_reg_review", False))
    default_rate = None
    rate_raw = await get_setting(session, "merchant_default_rate", "0.00")
    try:
        default_rate = Decimal(str(rate_raw))
    except Exception:
        default_rate = None

    for _ in range(3):
        pid = await generate_merchant_pid(session)
        m = Merchant(
            pid=pid,
            username=username,
            password=hash_password(body.password),
            invite_code=secrets.token_hex(8),
            email=email or None,
            phone=(body.phone or "").strip() or None,
            invited_by=invited_by,
            rate=default_rate,
            status=1,
            # 注册审核：待审核商户可登录但不可收款
            pay_status=2 if review else 1,
            # 下游通道编码：新商户默认必传（存量商户升级时保持 0，由批量开关过渡）
            channel_code_required=True,
        )
        set_api_secret(m)  # 开放 API 默认 MD5：注册即生成密钥
        session.add(m)
        try:
            await session.commit()
            break
        except Exception:
            await session.rollback()
            continue  # 并发 pid 冲突，顺延重试
    else:
        return err(-1, "注册失败，请重试")
    await session.refresh(m)

    # 欢迎消息（失败不阻断注册）
    try:
        from app.services.message import send_merchant_message
        notice = ("注册成功，支付权限待管理员审核。" if review
                  else "注册成功，完善结算信息后即可收款。")
        await send_merchant_message(
            session, m, title="欢迎入驻",
            content=notice + f" 商户ID（pid）：{m.pid}，请妥善保管密钥。",
            biz_type="system",
        )
        await session.commit()
    except Exception:
        await session.rollback()

    token = await _issue_login(session, cache, m, request, name="register")
    return suc({
        "pid": m.pid,
        "pay_status": m.pay_status,
        "token_type": token.token_type,
        "access_token": token.access_token,
        "expires_in": token.expires_in,
    })


# ============================================================
# 找回密码：邮件验证码（mail_channel 未配置时明确报错，引导联系管理员）
# ============================================================

class ForgotRequest(BaseModel):
    email: str


@router.post("/forgot", dependencies=[rate_limit(times=10, minutes=1)])
async def forgot_password(body: ForgotRequest, request: Request, session: SessionDep, cache: CacheDep):
    email = (body.email or "").strip().lower()
    if not email:
        return err(-1, "请填写邮箱")
    m = (await session.exec(
        select(Merchant).where(Merchant.email == email)
    )).first()
    if not m or m.status != 1:
        # 不暴露邮箱是否存在
        return suc("如果该邮箱已绑定商户，将收到重置验证码")
    # 60 秒频控
    if await cache.get(f"pwdreset:cooldown:{m.id}"):
        return err(-1, "发送过于频繁，请稍后再试")

    from app.services.message import send_mail
    import secrets
    code = str(secrets.randbelow(1000000)).zfill(6)
    ok = await send_mail(session, email, "密码重置验证码",
                          f"您正在重置商户后台登录密码，验证码：<b>{code}</b>，15 分钟内有效。")
    if not ok:
        return err(-1, "邮件通道未配置或发送失败，请联系管理员重置密码")
    await cache.set(f"pwdreset:code:{m.id}", code, ex=15 * 60)
    await cache.set(f"pwdreset:cooldown:{m.id}", "1", ex=60)
    return suc("如果该邮箱已绑定商户，将收到重置验证码")


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    password: str


@router.post("/reset", dependencies=[rate_limit(times=10, minutes=1)])
async def reset_password(body: ResetPasswordRequest, request: Request, session: SessionDep, cache: CacheDep):
    """凭邮件验证码重置密码。

    P1-11：6 位码 15 分钟有效，没有失败次数上限时可被在线爆破；
    这里按商户维度计失败次数（5 次/窗口），达上限即作废验证码。
    P1-10：重置成功吊销该商户全部登录令牌（改密后被盗会话立即失效）。
    """
    email = (body.email or "").strip().lower()
    if len(body.password or "") < 6:
        return err(-1, "新密码至少 6 位")
    m = (await session.exec(
        select(Merchant).where(Merchant.email == email)
    )).first()
    if not m or m.status != 1:
        return err(-1, "验证码错误或已过期")

    code_key = f"pwdreset:code:{m.id}"
    fail_key = f"pwdreset:fail:{m.id}"
    try:
        fails = int(await cache.get(fail_key) or 0)
    except (TypeError, ValueError):
        fails = 0
    if fails >= RESET_MAX_ATTEMPTS:
        await cache.delete(code_key)
        return err(-1, "验证码错误次数过多，请重新获取验证码")

    cached = await cache.get(code_key)
    if not cached or str(cached) != body.code.strip():
        try:
            await cache.set(fail_key, str(fails + 1), ex=RESET_ATTEMPT_WINDOW)
        except Exception:
            pass
        if fails + 1 >= RESET_MAX_ATTEMPTS:
            await cache.delete(code_key)
            return err(-1, "验证码错误次数过多，请重新获取验证码")
        return err(-1, "验证码错误或已过期")

    await cache.delete(code_key)
    await cache.delete(fail_key)
    m.password = hash_password(body.password)
    session.add(m)
    await revoke_all_tokens("merchant", m.id, session, cache=cache)
    await session.commit()
    return suc("密码重置成功，请使用新密码登录")
