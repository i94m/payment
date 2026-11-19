"""商户首页「收款信息」卡：基础资料 + 专属收款链接。

- 链接与聚合收款设置页同口径（/p/{短码}），惰性生成并终身复用
- paypage_enable 关闭时下发 False，由前端提示链接暂不可用
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


async def _profile(client, session) -> dict:
    resp = await client.get("/merchant-api/dashboard/profile", headers=await _headers(session))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    return body["data"]


@pytest.mark.asyncio
async def test_profile_basic_info_and_link(client, session):
    data = await _profile(client, session)
    assert data["pid"] == 1001
    assert data["username"] == "testmch"
    assert data["paypage_enable"] is True
    assert data["link"].startswith("/p/")
    assert data["created_at"]

    # 再次获取链接不变（惰性生成 + 终身复用）
    assert (await _profile(client, session))["link"] == data["link"]


@pytest.mark.asyncio
async def test_profile_reports_paypage_switch(client, session):
    await set_settings(session, {"paypage_enable": False})
    assert (await _profile(client, session))["paypage_enable"] is False
