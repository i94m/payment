"""登录会话策略：LOGIN_MULTI_SESSION 控制「单端 / 多端并存」。

- 默认（false）：同一账号只有一个有效会话，新登录撤销（并拉黑）旧会话
- 开启（true）：多端/多浏览器同时在线，互不影响
- 与配置无关的特例：SSO 代登录始终不撤销被代登录方已有的会话
"""
import pytest

from app.core.config import Config, config
from app.core.security import hash_password
from app.models import Agent, Merchant, SystemAdmin


async def _make_admin(session, username: str = "session-admin") -> SystemAdmin:
    admin = SystemAdmin(
        username=username, name="会话测试",
        password=hash_password("admin123456"), is_super_admin=True,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return admin


async def _login(client, url: str, username: str, password: str) -> str:
    r = await client.post(url, json={
        "username": username, "password": password, "captcha_code": "0000"})
    body = r.json()
    assert body["code"] == 0, body
    return body["data"]["access_token"]


async def _profile_status(client, url: str, token: str) -> int:
    r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code


def test_multi_session_default_off():
    """默认必须是单端登录（配置缺省 false，不能因为改了别的默认值而放开）。"""
    assert Config.model_fields["LOGIN_MULTI_SESSION"].default is False


@pytest.mark.asyncio
async def test_admin_login_single_session_by_default(client, seeded, session, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MULTI_SESSION", False)
    await _make_admin(session)

    t1 = await _login(client, "/admin-api/auth/login", "session-admin", "admin123456")
    assert await _profile_status(client, "/admin-api/auth/profile", t1) == 200

    t2 = await _login(client, "/admin-api/auth/login", "session-admin", "admin123456")
    # 默认单端：新登录把旧 token 拉黑，旧端被踢
    assert await _profile_status(client, "/admin-api/auth/profile", t1) == 401
    assert await _profile_status(client, "/admin-api/auth/profile", t2) == 200


@pytest.mark.asyncio
async def test_admin_login_multi_session_when_enabled(client, seeded, session, monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MULTI_SESSION", True)
    await _make_admin(session)

    t1 = await _login(client, "/admin-api/auth/login", "session-admin", "admin123456")
    t2 = await _login(client, "/admin-api/auth/login", "session-admin", "admin123456")
    # 多端并存：两个会话都有效
    assert await _profile_status(client, "/admin-api/auth/profile", t1) == 200
    assert await _profile_status(client, "/admin-api/auth/profile", t2) == 200


@pytest.mark.asyncio
async def test_merchant_login_follows_config(client, seeded, session, monkeypatch):
    m: Merchant = seeded["merchant"]
    assert m.username == "testmch"

    monkeypatch.setattr(config, "LOGIN_MULTI_SESSION", False)
    t1 = await _login(client, "/merchant-api/auth/login", "testmch", "test123456")
    t2 = await _login(client, "/merchant-api/auth/login", "testmch", "test123456")
    assert await _profile_status(client, "/merchant-api/auth/profile", t1) == 401
    assert await _profile_status(client, "/merchant-api/auth/profile", t2) == 200

    monkeypatch.setattr(config, "LOGIN_MULTI_SESSION", True)
    t3 = await _login(client, "/merchant-api/auth/login", "testmch", "test123456")
    t4 = await _login(client, "/merchant-api/auth/login", "testmch", "test123456")
    assert await _profile_status(client, "/merchant-api/auth/profile", t3) == 200
    assert await _profile_status(client, "/merchant-api/auth/profile", t4) == 200


@pytest.mark.asyncio
async def test_agent_login_follows_config(client, seeded, session, monkeypatch):
    m: Merchant = seeded["merchant"]
    session.add(Agent(merchant_id=m.id, name="agent-session",
                      password=hash_password("agent123"), status=1))
    await session.commit()

    monkeypatch.setattr(config, "LOGIN_MULTI_SESSION", False)
    t1 = await _login(client, "/agent-api/auth/login", "agent-session", "agent123")
    t2 = await _login(client, "/agent-api/auth/login", "agent-session", "agent123")
    assert await _profile_status(client, "/agent-api/auth/profile", t1) == 401
    assert await _profile_status(client, "/agent-api/auth/profile", t2) == 200

    monkeypatch.setattr(config, "LOGIN_MULTI_SESSION", True)
    t3 = await _login(client, "/agent-api/auth/login", "agent-session", "agent123")
    t4 = await _login(client, "/agent-api/auth/login", "agent-session", "agent123")
    assert await _profile_status(client, "/agent-api/auth/profile", t3) == 200
    assert await _profile_status(client, "/agent-api/auth/profile", t4) == 200
