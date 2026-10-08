"""代理端认证（JWT guard=agent）：登录/SSO 代登录/登出/资料。

登录字段沿用 username（即代理名称），便于复用商户端登录页与 auth-store；
代理名称全局唯一（models.Agent），按 name 精确匹配单一账号。
"""
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlmodel import col, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import AgentUser, issue_token, revoke_token, verify_password
from app.lib.linktoken import paypage_url
from app.models import Agent, Merchant
from app.responses import err, suc
from app.services import paytoken

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_key: str = ""
    captcha_code: str = ""


class SsoLoginRequest(BaseModel):
    """商户 SSO 代登录：凭 merchant-api/agent/{id}/sso 下发的一次性 token 换取正式 JWT。"""
    token: str


async def _issue_login(session, request: Request, agent: Agent, name: str = ""):
    """签发 agent JWT 并顺带更新最后登录时间（失败不阻断）。"""
    ip = request.client.host if request.client else None
    jwt_token = await issue_token(
        guard="agent",
        user_id=agent.id,
        session=session,
        ip=ip,
        user_agent=request.headers.get("user-agent", ""),
        single_session=False,  # 代理多端并存（对齐老系统 30 天 cookie 语义）
        name=name or None,
    )
    try:
        agent.last_login_at = datetime.now()
        session.add(agent)
        await session.commit()
    except Exception:
        await session.rollback()
    return jwt_token


@router.post("/login")
async def login(body: LoginRequest, request: Request, session: SessionDep, cache: CacheDep):
    from app.lib.captcha import verify_captcha_code
    captcha_err = await verify_captcha_code(cache, body.captcha_key, body.captcha_code)
    if captcha_err:
        return err(-1, captcha_err)

    name = (body.username or "").strip()
    if not name or not body.password:
        return err(-1, "请输入代理名称和密码")
    # 代理名称全局唯一：单条精确匹配
    agent = (await session.exec(
        select(Agent).where(col(Agent.name) == name, Agent.status == 1)
    )).first()
    if not agent or not verify_password(body.password, agent.password):
        return err(-1, "代理不存在或密码错误")

    token = await _issue_login(session, request, agent)
    return suc({
        "token_type": token.token_type,
        "access_token": token.access_token,
        "expires_in": token.expires_in,
    })


@router.post("/sso")
async def sso_login(body: SsoLoginRequest, request: Request, session: SessionDep, cache: CacheDep):
    token = (body.token or "").strip()
    if not token:
        return err(-1, "登录凭证无效")
    try:
        agent_id = int(await cache.get(f"sso:agent:{token}") or 0)
    except (TypeError, ValueError):
        agent_id = 0
    if agent_id <= 0:
        return err(-1, "登录凭证已失效，请让商户重新发起")
    # 一次性：立即作废
    try:
        await cache.delete(f"sso:agent:{token}")
    except Exception:
        pass

    agent = await session.get(Agent, agent_id)
    if not agent:
        return err(-1, "代理不存在")
    if agent.status != 1:
        return err(-1, "代理已被禁用，请联系商户")

    # SSO 是商户代登录，不撤销代理已有会话
    jwt_token = await _issue_login(session, request, agent, name="sso")
    return suc({
        "token_type": jwt_token.token_type,
        "access_token": jwt_token.access_token,
        "expires_in": jwt_token.expires_in,
    })


@router.post("/logout")
async def logout(auth: AgentUser, session: SessionDep, cache: CacheDep):
    if auth.jti:
        await revoke_token(auth.jti, session, cache)
    return suc("success")


@router.get("/profile")
async def profile(auth: AgentUser, session: SessionDep):
    a = auth.user
    m = await session.get(Merchant, a.merchant_id)
    # 代理端单一角色：permissions=['*'] 使模板权限守卫全量放行
    return suc({
        "id": a.id,
        "name": a.name,
        "username": a.name,
        "remark": a.remark or "",
        "status": a.status,
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
        "last_login_at": a.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if a.last_login_at else "",
        "merchant_name": m.username if m else "",
        "merchant_pid": m.pid if m else 0,
        "link": paypage_url(await paytoken.get_or_create(session, a.merchant_id, a.id)) if m else "",
        "is_super_admin": True,
        "permissions": ["*"],
    })
