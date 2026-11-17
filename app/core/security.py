"""认证/授权：JWT + Sanctum 风格 Token 跟踪 + RBAC 权限。

- admin guard → SystemAdmin（system_permissions RBAC）
- merchant guard → Merchant（单一角色，merchant_id 数据隔离）
- agent guard → Agent（下级代理，agent_id 数据隔离，单一角色）
- 全部 async（AsyncSession）
"""
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import delete
from sqlmodel import select

from app.core.cache import CacheClient
from app.core.config import config
from app.core.deps import CacheDep, SessionDep
from app.core.modules import MODULES, get_module_by_guard
from app.models import (
    Agent,
    Merchant,
    PersonalAccessToken,
    SystemAdmin,
    SystemAdminPermission,
    SystemPermission,
)

_log = logging.getLogger(__name__)


# ============================================================
# 密码哈希
# ============================================================

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        pw = hashed_password.encode("utf-8")
        if pw.startswith(b"$2y$"):
            pw = b"$2b$" + pw[4:]
        return bcrypt.checkpw(plain_password.encode("utf-8"), pw)
    except Exception:
        return False


# ============================================================
# JWT Token
# ============================================================

class AuthToken:
    access_token: str
    expires_in: int
    token_type: str = "Bearer"

    def __init__(self, access_token: str, expires_in: int):
        self.access_token = access_token
        self.expires_in = expires_in
        self.token_type = "Bearer"


def create_token(guard: str, user_id: int, abilities: list[str] | None = None,
                 expires_minutes: int | None = None, jti: str | None = None) -> AuthToken:
    if jti is None:
        jti = secrets.token_hex(16)
    if expires_minutes is None:
        mod = get_module_by_guard(guard)
        if mod:
            expires_minutes = mod.token_ttl_minutes
        else:
            expires_minutes = config.API_ACCESS_TOKEN_TTL

    payload = {
        "sub": f"{guard}:{user_id}",
        "guard": guard,
        "abilities": abilities or [],
        "jti": jti,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
        "iat": datetime.now(timezone.utc),
    }
    access_token = jwt.encode(payload, config.APP_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
    return AuthToken(access_token=access_token, expires_in=expires_minutes * 60)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, config.APP_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def single_session_default() -> bool:
    """新签发 token 时是否按「单端登录」处理（撤销同账号其它会话）。

    由 .env 的 LOGIN_MULTI_SESSION 控制：false（默认）= 单端，新登录踢掉旧会话；
    true = 多端并存。SSO 代登录等场景不受此配置影响，仍显式传 single_session=False，
    避免管理员/商户代登录把对方正在用的会话撤掉。
    """
    return not config.LOGIN_MULTI_SESSION


# ============================================================
# Token 签发 / 撤销（DB 跟踪 + Redis 黑名单）
# ============================================================

async def _blacklist_jti(cache: CacheClient, jti: str) -> None:
    try:
        await cache.set(f"token:blacklist:{jti}", "1", ex=60 * 60 * 24 * 7)
    except Exception as e:
        _log.warning("写 Redis 黑名单失败 jti=%s: %s", jti, e)


async def issue_token(
    guard: str,
    user_id: int,
    session: SessionDep,
    *,
    name: str | None = None,
    abilities: list[str] | None = None,
    expires_minutes: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    single_session: bool = False,
    cache: CacheDep | None = None,
) -> AuthToken:
    jti = secrets.token_hex(16)
    if single_session:
        await revoke_all_tokens(guard, user_id, session, cache=cache)

    token = create_token(guard, user_id, abilities=abilities,
                         expires_minutes=expires_minutes, jti=jti)

    now = datetime.now()
    pat = PersonalAccessToken(
        guard=guard,
        user_id=user_id,
        name=name,
        token_id=jti,
        abilities=json.dumps(abilities) if abilities else None,
        ip=ip,
        user_agent=(user_agent or "")[:500],
        last_used_at=now,
        expires_at=now + timedelta(minutes=token.expires_in // 60),
    )
    session.add(pat)
    await session.commit()
    return token


async def revoke_token(jti: str, session: SessionDep, cache: CacheClient | None = None) -> bool:
    result = await session.exec(
        delete(PersonalAccessToken).where(PersonalAccessToken.token_id == jti)
    )
    await session.commit()
    if cache and result.rowcount > 0:
        await _blacklist_jti(cache, jti)
    return result.rowcount > 0


async def revoke_all_tokens(guard: str, user_id: int, session: SessionDep,
                            cache: CacheClient | None = None, except_jti: str | None = None) -> int:
    q = select(PersonalAccessToken.token_id).where(
        PersonalAccessToken.guard == guard,
        PersonalAccessToken.user_id == user_id,
    )
    if except_jti:
        q = q.where(PersonalAccessToken.token_id != except_jti)
    revoked_jtis = list((await session.exec(q)).all())

    dq = delete(PersonalAccessToken).where(
        PersonalAccessToken.guard == guard,
        PersonalAccessToken.user_id == user_id,
    )
    if except_jti:
        dq = dq.where(PersonalAccessToken.token_id != except_jti)
    result = await session.exec(dq)
    await session.commit()

    if cache and revoked_jtis:
        for jti in revoked_jtis:
            await _blacklist_jti(cache, jti)
    return result.rowcount


async def is_token_revoked(jti: str, cache: CacheClient) -> bool:
    try:
        val = await cache.get(f"token:blacklist:{jti}")
        return val is not None
    except Exception:
        return False


async def list_user_tokens(guard: str, user_id: int, session: SessionDep) -> list[PersonalAccessToken]:
    return list((await session.exec(
        select(PersonalAccessToken)
        .where(PersonalAccessToken.guard == guard, PersonalAccessToken.user_id == user_id)
        .order_by(PersonalAccessToken.created_at.desc())
    )).all())


async def dump_user_tokens(guard: str, user_id: int, current_jti: str | None,
                           session: SessionDep) -> list[dict]:
    from user_agents import parse as parse_ua
    tokens = await list_user_tokens(guard, user_id, session)
    items = []
    for t in tokens:
        ua = parse_ua(t.user_agent or "")
        items.append({
            "id": t.id,
            "name": t.name,
            "ip": t.ip,
            "device": f"{ua.os.family} {ua.os.version_string}" if ua.os.family else "",
            "browser": f"{ua.browser.family} {ua.browser.version_string}" if ua.browser.family else "",
            "last_used_at": t.last_used_at.strftime("%Y-%m-%d %H:%M:%S") if t.last_used_at else None,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None,
            "is_current": t.token_id == current_jti,
        })
    return items


async def revoke_user_token_by_id(token_id: int, guard: str, user_id: int,
                                  current_jti: str | None, session: SessionDep,
                                  cache: CacheClient | None = None) -> tuple[bool, str]:
    token = await session.get(PersonalAccessToken, token_id)
    if not token or token.guard != guard or token.user_id != user_id:
        return False, "令牌不存在"
    if current_jti and token.token_id == current_jti:
        return False, "不能撤销当前正在使用的令牌，请使用退出登录"
    await revoke_token(token.token_id, session, cache)
    return True, ""


# ============================================================
# Auth Guard
# ============================================================

_oauth2_schemes: dict[str, OAuth2PasswordBearer] = {}
for _mod_name, _mod_cfg in MODULES.items():
    _oauth2_schemes[_mod_cfg.guard] = OAuth2PasswordBearer(
        tokenUrl=f"{_mod_cfg.api_prefix}/auth/login",
        auto_error=False,
    )


class AuthResult:
    """认证结果（请求级权限缓存）"""

    def __init__(self, user, guard: str, jti: str | None = None):
        self.user = user
        self.guard = guard
        self.jti = jti
        self._permission_keys: set[str] | None = None

    async def get_permission_keys(self, session) -> set[str]:
        if self._permission_keys is None:
            admin = self.user
            if getattr(admin, "is_super_admin", False):
                self._permission_keys = set()
            else:
                rows = (await session.exec(
                    select(SystemPermission.key)
                    .join(SystemAdminPermission, SystemAdminPermission.permission_id == SystemPermission.id)
                    .where(SystemAdminPermission.admin_id == admin.id)
                )).all()
                self._permission_keys = set(rows)
        return self._permission_keys

    @property
    def is_super_admin(self) -> bool:
        return bool(getattr(self.user, "is_super_admin", False))


async def _get_current_user_by_guard(
    guard: str,
    token: str | None,
    session: SessionDep,
    cache: CacheDep,
) -> AuthResult:
    if not token:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Unauthorized."})

    payload = decode_token(token)
    if not payload or payload.get("guard") != guard:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Unauthorized."})

    jti = payload.get("jti")
    if jti and await is_token_revoked(jti, cache):
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Token已被撤销"})

    try:
        user_id = int(payload["sub"].split(":")[1])
    except (ValueError, IndexError, KeyError):
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Unauthorized."})

    if guard == "admin":
        model = SystemAdmin
    elif guard == "merchant":
        model = Merchant
    elif guard == "agent":
        model = Agent
    else:
        model = SystemAdmin

    user = await session.get(model, user_id)
    if not user:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "Unauthorized."})

    if guard == "admin" and getattr(user, "is_forbid", False):
        raise HTTPException(status_code=401, detail={"code": 401, "message": "账户已被锁定，请联系管理员！"})
    if guard == "merchant" and getattr(user, "status", 1) != 1:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "商户已被禁用，请联系管理员！"})
    if guard == "agent" and getattr(user, "status", 1) != 1:
        raise HTTPException(status_code=401, detail={"code": 401, "message": "代理已被禁用，请联系商户！"})

    return AuthResult(user=user, guard=guard, jti=jti)


# 各 guard 的认证依赖必须显式写成模块级函数（不用工厂闭包）：
# Cython 编译后函数注解会被字符串化，FastAPI 靠「注解字符串 + 模块全局名」
# eval 还原依赖 —— 注解里引用闭包变量（工厂里的 scheme）在编译产物中必然
# 解析失败，端点会把 token/cache 当成必填 query 参数。scheme 因此取自模块级
# _oauth2_schemes 并以模块级名字被注解引用。
_scheme_admin = _oauth2_schemes["admin"]
_scheme_merchant = _oauth2_schemes["merchant"]
_scheme_agent = _oauth2_schemes["agent"]


async def get_current_admin(
    token: Annotated[str | None, Depends(_scheme_admin)],
    session: SessionDep,
    cache: CacheDep,
) -> AuthResult:
    return await _get_current_user_by_guard("admin", token, session, cache)


async def get_current_merchant(
    token: Annotated[str | None, Depends(_scheme_merchant)],
    session: SessionDep,
    cache: CacheDep,
) -> AuthResult:
    return await _get_current_user_by_guard("merchant", token, session, cache)


async def get_current_agent(
    token: Annotated[str | None, Depends(_scheme_agent)],
    session: SessionDep,
    cache: CacheDep,
) -> AuthResult:
    return await _get_current_user_by_guard("agent", token, session, cache)

AdminUser = Annotated[AuthResult, Depends(get_current_admin)]
MerchantUser = Annotated[AuthResult, Depends(get_current_merchant)]
AgentUser = Annotated[AuthResult, Depends(get_current_agent)]


# ============================================================
# 权限检查
# ============================================================

class PermissionChecker:
    """RBAC 权限检查依赖。

    用法:
        @router.get("/x")
        async def x(auth: AdminUser, _: Annotated[None, Depends(PermissionChecker("order.view"))]):
            ...
    """

    def __init__(self, permission: str):
        self.permission = permission

    async def __call__(self, auth: AdminUser, session: SessionDep) -> None:
        if auth.is_super_admin:
            return
        keys = await auth.get_permission_keys(session)
        if self.permission not in keys:
            raise HTTPException(status_code=403, detail={"code": 403, "message": "forbidden"})


async def check_permission(auth: AuthResult, permission: str, session) -> bool:
    """过程式权限检查（路由内手动调用）。"""
    if auth.is_super_admin:
        return True
    return permission in await auth.get_permission_keys(session)


async def require_super_admin(auth: AdminUser) -> None:
    """超管专属依赖（fail-closed）。

    用于「权限键不足以表达风险」的操作：插件安装会把 .so 直接 import 进进程，
    等价于任意代码执行，任何非超管账号都不应触发，哪怕被授予 channel.update。
    """
    if not auth.is_super_admin:
        raise HTTPException(status_code=403,
                            detail={"code": 403, "message": "该操作仅限超级管理员"})
