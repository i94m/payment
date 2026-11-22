"""商户面板「对接信息」：apikey 接口带出复制用的真实配置。

面板把这段数据拼成「字段：值」纯文本给接入方，所以接口至少要保证：
名称/商户号真实、业务编码只给一个（测试编码优先）、不再下发站点地址
（下单/查询/文档域名由前端取 window.location.origin）。
"""
import pytest
from sqlmodel import select

from app.core.security import issue_token
from app.models import ChannelCode, Merchant, Setting


async def _merchant_headers(session):
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    tk = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {tk.access_token}"}


@pytest.mark.asyncio
async def test_apikey_exposes_integration_info(client, seeded, session):
    session.add(Setting(key="site_url", value="https://p.example.com/"))
    session.add_all([
        ChannelCode(code="1001", name="正式通道", status=1),
        ChannelCode(code="1002", name="测试通道", status=1),
        ChannelCode(code="9999", name="停用编码", status=0),
    ])
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]

    assert data["username"] == "testmch"
    assert data["pid"] == 1001
    # 只给一个编码：启用中的测试编码优先，不再并列多个
    assert data["channel_code"] == {"code": "1002", "name": "测试通道"}
    assert data["channel_code_required"] is False
    # 域名交给前端 window.location.origin，后端不再下发站点地址
    assert "site_url" not in data
    assert "channel_codes" not in data


@pytest.mark.asyncio
async def test_apikey_channel_code_falls_back_to_first_enabled(client, seeded, session):
    """没有测试编码时退回第一个启用编码；停用编码任何时候都不出现。"""
    session.add_all([
        ChannelCode(code="b001", name="通道B", status=1),
        ChannelCode(code="a001", name="通道A", status=1),
        ChannelCode(code="a000", name="停用A", status=0),
    ])
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["channel_code"] == {"code": "a001", "name": "通道A"}


@pytest.mark.asyncio
async def test_apikey_channel_code_empty_when_none_enabled(client, seeded, session):
    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["channel_code"] is None
