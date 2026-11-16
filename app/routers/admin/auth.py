"""admin 认证（SystemAdmin）。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import select

from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.core.security import (
    AdminUser,
    dump_user_tokens,
    issue_token,
    revoke_token,
    revoke_user_token_by_id,
    verify_password,
)
from app.models import SystemAdmin, SystemAdminLogin
from app.responses import err, suc

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_key: str = ""
    captcha_code: str = ""


@router.post("/login", dependencies=[rate_limit(times=10, minutes=1)])
async def login(body: LoginRequest, request: Request, session: SessionDep, cache: CacheDep):
    from app.lib.captcha import verify_captcha_code
    captcha_err = await verify_captcha_code(cache, body.captcha_key, body.captcha_code)
    if captcha_err:
        return err(-1, captcha_err)

    row = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == body.username)
    )).first()
    if not row or not verify_password(body.password, row.password):
        return err(-1, "用户名或密码错误")
    if row.is_forbid:
        return err(-1, "账户已被禁用")

    token = await issue_token(
        guard="admin",
        user_id=row.id,
        session=session,
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", ""),
        single_session=True,
        cache=cache,
    )
    ip = request.client.host if request.client else None
    row.last_active_at = datetime.now()
    row.last_active_ip = ip
    session.add(row)
    # system_admin_logins：登录留痕（此前是死表，P1-7 一并启用）
    session.add(SystemAdminLogin(
        admin_id=row.id, ip=(ip or "")[:45],
        user_agent=(request.headers.get("user-agent", "") or "")[:500],
    ))
    await session.commit()

    return suc({
        "token_type": token.token_type,
        "access_token": token.access_token,
        "expires_in": token.expires_in,
    })


@router.post("/logout")
async def logout(auth: AdminUser, session: SessionDep, cache: CacheDep):
    if auth.jti:
        await revoke_token(auth.jti, session, cache)
    return suc("success")


@router.get("/profile")
async def profile(auth: AdminUser, session: SessionDep):
    user = auth.user
    return suc({
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "is_super_admin": bool(user.is_super_admin),
        "permissions": sorted(await auth.get_permission_keys(session)),
    })


@router.get("/tokens")
async def tokens(auth: AdminUser, session: SessionDep):
    return suc(await dump_user_tokens("admin", auth.user.id, auth.jti, session))


@router.delete("/tokens/{token_id}")
async def delete_token(token_id: int, auth: AdminUser, session: SessionDep):
    ok, msg = await revoke_user_token_by_id(token_id, "admin", auth.user.id, auth.jti, session)
    if not ok:
        return err(-1, msg)
    return suc("success")
