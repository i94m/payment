"""商户面板「对接信息」：apikey 接口带出复制用的真实配置。

面板把这段数据拼成「字段：值」纯文本给接入方，所以接口至少要保证：
名称/商户号真实、站点地址可直接拼路径、业务编码只出现启用中的编码。
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
    # 去掉尾部斜杠：前端直接拼 /api/orders、/api/orders/{trade_no}、/docs
    assert data["site_url"] == "https://p.example.com"
    # 只下发启用中的编码（停用编码下单必失败，不该出现在对接信息里）
    assert data["channel_codes"] == [{"code": "1002", "name": "测试通道"}]
    assert data["channel_code_required"] is False


@pytest.mark.asyncio
async def test_apikey_site_url_falls_back_to_request_host(client, seeded, session):
    """未配置站点地址时回退当前请求域名，避免对接信息里出现空地址。"""
    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["site_url"] == "http://testserver"
    assert body["data"]["channel_codes"] == []
