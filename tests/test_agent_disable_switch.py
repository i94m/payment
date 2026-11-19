"""下级代理总开关（agent_enable）：站点配置页可改 + 菜单隐藏 + 接口级拦截。

开关语义（FREE-98 需求 1）：
- 开（默认）：商户端「下级代理」整组可见，商户/代理接口正常
- 关：菜单整组隐藏；商户端 agent 接口 403；代理端拒绝登录/SSO，已有会话的
  业务接口 403（登出仍放行，避免旧 token 卡死）；开关在站点配置页可改
"""
import pytest
from sqlmodel import select

from app.models import Merchant
from app.services.settings import set_settings


async def _headers(session) -> dict:
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    from app.core.security import issue_token
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _admin_headers(session) -> dict:
    from app.core.security import hash_password, issue_token
    from app.models import SystemAdmin

    admin = (await session.exec(select(SystemAdmin))).first()
    if admin is None:
        admin = SystemAdmin(name="管理员", username="admin",
                            password=hash_password("admin123"), is_super_admin=True)
        session.add(admin)
        await session.commit()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _create_agent(client, session, name: str = "switch-agent") -> dict:
    """开关开启时先建一个代理（关闭后不再允许建）。"""
    resp = await client.post("/merchant-api/agent", headers=await _headers(session),
                             json={"name": name, "password": "agent123"})
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


async def _agent_login(client, name: str) -> dict:
    return (await client.post("/agent-api/auth/login", json={
        "username": name, "password": "agent123", "captcha_code": "0000"})).json()


@pytest.mark.asyncio
async def test_switch_exposed_in_admin_settings_page(client, seeded, session):
    """站点配置页可读写（运维日常开关，不需要写库）。"""
    adm = await _admin_headers(session)

    keys = {i["key"] for i in (await client.get("/admin-api/setting", headers=adm)).json()["data"]["items"]}
    assert "agent_enable" in keys

    r = await client.patch("/admin-api/setting", json={"agent_enable": False}, headers=adm)
    assert r.json()["code"] == 0, r.json()

    features = (await client.get("/merchant-api/site-info")).json()["data"]["features"]
    assert features["agent_enable"] is False

    # 幂等恢复默认（本用例独占 DB，恢复只是保持语义清晰）
    r2 = await client.patch("/admin-api/setting", json={"agent_enable": True}, headers=adm)
    assert r2.json()["code"] == 0, r2.json()


@pytest.mark.asyncio
async def test_switch_off_hides_menu_group(client, seeded, session):
    await set_settings(session, {"agent_enable": False})
    sections = (await client.get("/merchant-api/menuList", headers=await _headers(session))).json()["data"]
    assert "下级代理" not in [s["label"] for s in sections]


@pytest.mark.asyncio
async def test_switch_off_blocks_merchant_agent_api(client, seeded, session):
    """只隐藏菜单不够：直访 URL / 直接调接口也必须被拒。"""
    agent = await _create_agent(client, session)
    h = await _headers(session)

    # 开启时可用
    assert (await client.get("/merchant-api/agent/list", headers=h)).json()["code"] == 0

    await set_settings(session, {"agent_enable": False})

    for method, path, body in [
        ("get", "/merchant-api/agent/list", None),
        ("get", "/merchant-api/agent/performance", None),
        ("get", "/merchant-api/agent/performance/list", None),
        ("post", "/merchant-api/agent", {"name": "new-agent", "password": "123456"}),
        ("put", f"/merchant-api/agent/{agent['id']}", {"remark": "x"}),
        ("delete", f"/merchant-api/agent/{agent['id']}", None),
        ("post", f"/merchant-api/agent/{agent['id']}/sso", None),
    ]:
        kwargs = {"headers": h}
        if body is not None:
            kwargs["json"] = body
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 403, (method, path, resp.text)
        assert "下级代理功能已关闭" in resp.json()["message"]

    # 开启后恢复
    await set_settings(session, {"agent_enable": True})
    assert (await client.get("/merchant-api/agent/list", headers=h)).json()["code"] == 0


@pytest.mark.asyncio
async def test_switch_off_blocks_agent_panel_login_and_business(client, seeded, session):
    """代理端：关闭后不签发新会话，已有 token 也不能读业务数据。"""
    await _create_agent(client, session, "panel-switch-agent")

    body = await _agent_login(client, "panel-switch-agent")
    assert body["code"] == 0, body
    h = {"Authorization": f"Bearer {body['data']['access_token']}"}
    assert (await client.get("/agent-api/dashboard", headers=h)).json()["code"] == 0

    await set_settings(session, {"agent_enable": False})

    # 登录 / SSO 被拒（业务错误文案，不是「密码错误」）
    login = await _agent_login(client, "panel-switch-agent")
    assert login["code"] != 0
    assert "下级代理功能已关闭" in login["message"]

    sso = (await client.post("/agent-api/auth/sso", json={"token": "whatever"})).json()
    assert sso["code"] != 0
    assert "下级代理功能已关闭" in sso["message"]

    # 已有会话：业务接口 403
    for path in ("/agent-api/auth/profile", "/agent-api/dashboard", "/agent-api/order/list"):
        resp = await client.get(path, headers=h)
        assert resp.status_code == 403, (path, resp.text)
        assert "下级代理功能已关闭" in resp.json()["message"]

    # 登出仍放行：避免关闭开关后旧 token 无法主动作废
    logout = await client.post("/agent-api/auth/logout", headers=h)
    assert logout.status_code == 200 and logout.json()["code"] == 0
