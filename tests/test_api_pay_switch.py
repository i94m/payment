"""下游 API 支付接口开关（api_pay_enable，FREE-33 需求 2）。

- 开（默认）：开放 API 正常下单/查单，商户端菜单含「API信息」
- 关：开放 API 拒绝下单/查单（明确错误信息）；商户端隐藏「API信息」菜单入口，
  但页面与逻辑保留（/merchant-api/account/apikey 直访仍可用）；收款页/收银台不受影响
- 管理端支付设置页可见并可在 UI 编辑该开关
"""
import pytest
from sqlmodel import select

from app.models import Merchant
from app.services.settings import set_settings
from tests.conftest import pay_code, signed_params


async def _order_params(sign: str | None = None, **extra) -> dict:
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "AP1",
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "开关测试", "money": "10.00",
    }
    params.update(extra)
    params = signed_params(params)
    if sign is not None:
        params["sign"] = sign
    return params


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


@pytest.mark.asyncio
async def test_default_open_api_works(client, seeded):
    resp = await client.post("/api/orders", json=await _order_params())
    assert resp.json()["code"] == 0


@pytest.mark.asyncio
async def test_site_info_includes_api_pay_enable(client):
    features = (await client.get("/merchant-api/site-info")).json()["data"]["features"]
    assert features["api_pay_enable"] is True


@pytest.mark.asyncio
async def test_disabled_rejects_create_and_query(client, session):
    await set_settings(session, {"api_pay_enable": False})

    resp = await client.post("/api/orders", json=await _order_params())
    body = resp.json()
    assert resp.status_code == 400
    assert body["code"] != 0
    assert "API支付接口未开放" in body["message"]

    # 验签通过的查单同样拒绝（与下单一致的开关口径）
    params = signed_params({"pid": "1001"})
    resp2 = await client.get("/api/orders/SOMETRADE", params=params)
    assert resp2.status_code == 400
    assert "API支付接口未开放" in resp2.json()["message"]

    # 签名错误仍优先报验签失败（开关判定在验签之后）
    resp3 = await client.post("/api/orders", json=await _order_params(sign="bad"))
    assert "签名" in resp3.json()["message"]


@pytest.mark.asyncio
async def test_disabled_hides_menu_but_keeps_page(client, session):
    await set_settings(session, {"api_pay_enable": False})

    # 菜单隐藏「API信息」入口，同组其余项保留
    resp = await client.get("/merchant-api/menuList", headers=await _headers(session))
    sections = resp.json()["data"]
    profile = next(s for s in sections if s["label"] == "个人资料")
    names = [i["name"] for i in profile["items"]]
    assert "API信息" not in names
    assert names == ["修改资料", "修改密码"]

    # 页面与逻辑保留：API信息数据接口直访仍可用
    resp2 = await client.get("/merchant-api/account/apikey", headers=await _headers(session))
    assert resp2.status_code == 200
    assert resp2.json()["data"]["public_key"]
    assert resp2.json()["data"]["platform_public_key"]

    # /site-info 同步下发开关（前端侧边栏同款过滤）
    features = (await client.get("/merchant-api/site-info")).json()["data"]["features"]
    assert features["api_pay_enable"] is False


@pytest.mark.asyncio
async def test_disabled_keeps_paypage_and_cashier(client, session):
    """开关只影响下游 API：聚合收款页建单与收银台不受影响。"""
    await set_settings(session, {"api_pay_enable": False})

    resp = await client.post("/paypage/order", json={"token": await pay_code(session), "money": "5.00"})
    assert resp.json()["code"] == 0
    trade_no = resp.json()["data"]["trade_no"]

    resp2 = await client.get("/cashier/types", params={"trade_no": trade_no})
    assert resp2.json()["code"] == 0


@pytest.mark.asyncio
async def test_admin_settings_page_exposes_switch(client, session):
    """管理端支付设置页可见该开关（可 UI 编辑）。"""
    resp = await client.get("/admin-api/setting", headers=await _admin_headers(session))
    keys = {i["key"] for i in resp.json()["data"]["items"]}
    assert "api_pay_enable" in keys

    # 写入走同一校验通道
    resp2 = await client.patch("/admin-api/setting", json={"api_pay_enable": False},
                               headers=await _admin_headers(session))
    assert resp2.json()["code"] == 0
    resp3 = await client.post("/api/orders", json=await _order_params())
    assert "API支付接口未开放" in resp3.json()["message"]


@pytest.mark.asyncio
async def test_api_order_rate_limit_configurable(client, session):
    """下单限流为运行期配置项：默认 60、管理端设置页可见可改、0=不限合法。"""
    h = await _admin_headers(session)
    resp = await client.get("/admin-api/setting", headers=h)
    item = next(i for i in resp.json()["data"]["items"]
                if i["key"] == "api_order_rate_limit")
    assert item["value"] == 60 and item["type"] == "int"

    resp2 = await client.patch("/admin-api/setting",
                               json={"api_order_rate_limit": 120}, headers=h)
    assert resp2.json()["code"] == 0
    resp3 = await client.get("/admin-api/setting", headers=h)
    item3 = next(i for i in resp3.json()["data"]["items"]
                 if i["key"] == "api_order_rate_limit")
    assert item3["value"] == 120

    # 0=不限 合法；类型不符整体拒绝
    ok0 = await client.patch("/admin-api/setting",
                             json={"api_order_rate_limit": 0}, headers=h)
    assert ok0.json()["code"] == 0
    bad = await client.patch("/admin-api/setting",
                             json={"api_order_rate_limit": "abc"}, headers=h)
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_rate_limit_setting_dep_behavior(client, seeded, session, monkeypatch):
    """动态限流依赖：无 Redis 短路放行；配置 0 不限流；配置 N 按 N 计数。"""
    from app.core import limiter as L
    from app.services.settings import set_settings

    inner = L.rate_limit_setting("api_order_rate_limit", 60).dependency
    # 无 Redis（限流器未初始化）：直接放行，不读设置
    assert await inner(request=None, response=None, session=None, cache=None) is None

    calls: list[int] = []

    class FakeRateLimiter:
        def __init__(self, times, minutes):
            calls.append(times)

        async def __call__(self, request, response):
            return None

    monkeypatch.setattr(L, "RateLimiter", FakeRateLimiter)
    monkeypatch.setattr(L.FastAPILimiter, "redis", object())  # 模拟已初始化

    await set_settings(session, {"api_order_rate_limit": 0})   # 0=不限
    await inner(request=None, response=None, session=session, cache=None)
    assert calls == []

    await set_settings(session, {"api_order_rate_limit": 120})
    await inner(request=None, response=None, session=session, cache=None)
    assert calls == [120]
