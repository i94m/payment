"""通道后台页面相关回归：

- 通道测试不需要任何站点配置项（测试商户自动创建，无需配测试商户 PID）
- 通道测试会落测试单（order_type=2）：可查、可回调查看状态，但不进资金/统计
- 商户端「测试支付」走与管理端通道测试同一逻辑（挂系统测试商户），商户侧无感
- 通道列表「调试日志」开关走独立接口，只改 debug_log，不重写费率/限额
- 插件 inputs 返回前端渲染口径的 label（插件 meta 里叫 title），且易支付
  不再暴露 appswitch 字段
"""
import pytest
from sqlmodel import func, select

from app.core.security import hash_password, issue_token
from app.models import Channel, Merchant, MerchantRecord, Order, SystemAdmin
from app.services.balance import dmoney
from tests.conftest import UPSTREAM_KEY
from tests.test_pay_flow import _notify_params

MASK = "******"


async def _admin_headers(session, username: str = "chanadmin"):
    admin = SystemAdmin(name="管理员", username=username,
                        password=hash_password("admin123456"), is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _merchant_headers(session) -> dict:
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.mark.asyncio
async def test_no_test_pay_settings_exposed(session, client, seeded):
    """通道测试没有任何站点配置项：测试商户自动创建，不需要开关/PID。"""
    headers = await _admin_headers(session)

    r = await client.get("/admin-api/setting", headers=headers)
    assert r.status_code == 200
    keys = {item["key"] for item in r.json()["data"]["items"]}
    assert "test_pay_enable" not in keys
    assert "test_pay_merchant_pid" not in keys

    # 注册表已删除该键：即使是旧客户端提交也会被白名单/注册表拒绝
    r = await client.patch("/admin-api/setting",
                           json={"test_pay_enable": False}, headers=headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_channel_testpay_creates_test_order_and_isolated(client, seeded, session):
    """通道测试落测试单：回调查得到、置已支付，但不入余额/统计，也不占通道限额。"""
    headers = await _admin_headers(session)
    channel = seeded["channel"]

    r = await client.post(f"/admin-api/pay/channels/{channel.id}/testpay",
                          json={"name": "支付测试", "amount": "1.00"}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["pay_type"] == "jump"
    assert body["data"]["pay_info"].startswith("https://")
    trade_no = body["data"]["trade_no"]
    assert trade_no.startswith("TEST")

    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    assert order.order_type == 2
    assert order.status == 0
    assert str(order.money) == "1.00"
    assert str(order.realmoney) == "1.00"
    # 测试单也按同一费率口径算账（通道商户费率 2% / 成本费率 1%）：联调要能当场核对
    # 手续费与平台利润，但仍不入余额/不返佣/不进统计（下面逐项验证）
    assert order.merchant_income == dmoney("0.98")
    assert order.channel_cost == dmoney("0.01")
    assert order.profit == dmoney("0.01")
    assert order.notify_url in (None, "")
    merchant = await session.get(Merchant, order.merchant_id)
    assert merchant.pid == 100000  # 内置系统测试商户
    assert merchant.status == 0

    # 回调闭环：验签通过 → 订单置已支付，但不入余额、不写流水、不排队通知
    resp = await client.post(f"/callbacks/epay/{trade_no}",
                             params=_notify_params(order, money="1.00"))
    assert resp.text == "success", resp.text
    await session.refresh(order)
    assert order.status == 1
    assert order.notify_status == 0
    assert order.merchant_income == dmoney("0.98")

    await session.refresh(merchant)
    assert merchant.balance == 0
    records = (await session.exec(select(MerchantRecord).where(
        MerchantRecord.order_trade_no == trade_no))).all()
    assert records == []

    # 通道/支付方式今日收款、管理端首页口径都不含测试单
    r = await client.get("/admin-api/pay/channels/stats", headers=headers)
    assert r.json()["data"] == {}
    r = await client.get("/admin-api/pay/types/stats", headers=headers)
    assert r.json()["data"] == {}
    r = await client.get("/admin-api/dashboard", headers=headers)
    data = r.json()["data"]
    assert data["count1"] == 0       # 订单总数
    assert data["today"]["count"] == 0
    assert data["count2"] == 1       # 商户数（不含系统测试商户）

    # 测试单在管理端订单列表里显式标记类型，默认（不筛类型）也能看到（联调要能点进去看）
    r = await client.post("/admin-api/order/list", json={}, headers=headers)
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["order_type"] == 2
    # 金额字段原样下发：利润算出来是 0.01 就显示 0.01（不能因为 falsy 被吞成空串）
    assert items[0]["profit"] == "0.01"
    assert items[0]["merchant_income"] == "0.98"
    assert items[0]["channel_cost"] == "0.01"

    r = await client.post("/admin-api/order/list", json={"order_type": "2",
                                                         "trade_no": trade_no},
                          headers=headers)
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["order_type"] == 2

    # 显式只看正式收款时不掺测试单
    r = await client.post("/admin-api/order/list", json={"order_type": "0"}, headers=headers)
    assert r.json()["data"]["total"] == 0

    # 按平台订单号精确查询（「查看订单」按钮直达）
    r = await client.post("/admin-api/order/list", json={"trade_no": trade_no},
                          headers=headers)
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["trade_no"] == trade_no


@pytest.mark.asyncio
async def test_zero_profit_is_serialized_as_zero_not_blank(client, seeded, session):
    """利润正好 0 元时也要下发 "0.00"。

    `Decimal("0.00") or ""` 在 Python 里是空串，后台会把它渲染成「—」，
    看起来像平台利润没算出来（商户费率 == 成本费率、测试单都会命中）。
    """
    headers = await _admin_headers(session)
    channel = seeded["channel"]
    channel.merchant_rate = dmoney("2.00")
    channel.cost_rate = dmoney("2.00")   # 费率与成本相等 → 平台利润 0.00
    session.add(channel)
    await session.commit()

    r = await client.post(f"/admin-api/pay/channels/{channel.id}/testpay",
                          json={"name": "零利润", "amount": "10.00"}, headers=headers)
    assert r.status_code == 200, r.text
    trade_no = r.json()["data"]["trade_no"]

    r = await client.post("/admin-api/order/list", json={"trade_no": trade_no}, headers=headers)
    item = r.json()["data"]["items"][0]
    assert item["profit"] == "0.00"
    assert item["merchant_income"] == "9.80"
    assert item["channel_cost"] == "0.20"

    r = await client.get(f"/admin-api/order/{trade_no}", headers=headers)
    detail = r.json()["data"]
    assert detail["profit"] == "0.00"
    assert detail["channel_cost"] == "0.20"


@pytest.mark.asyncio
async def test_merchant_testpay_goes_to_system_test_merchant(client, seeded, session):
    """商户端测试支付：与管理端通道测试同逻辑，测试单挂系统测试商户，商户侧全程无感。"""
    headers = await _merchant_headers(session)
    merchant = seeded["merchant"]

    r = await client.post("/merchant-api/testpay/submit",
                          json={"type": "alipay", "amount": "2.00", "name": "测试支付"},
                          headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    trade_no = body["data"]["trade_no"]
    assert trade_no.startswith("TEST")

    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    assert order.order_type == 2
    owner = await session.get(Merchant, order.merchant_id)
    assert owner.pid == 100000  # 内置系统测试商户，不是发起测试的商户
    assert order.merchant_id != merchant.id

    resp = await client.post(f"/callbacks/epay/{trade_no}",
                             params=_notify_params(order, money="2.00"))
    assert resp.text == "success", resp.text
    await session.refresh(order)
    assert order.status == 1

    # 商户自己的订单记录里看不到这笔测试单（默认 / 全部类型 / 筛测试单都没有）
    for body in ({}, {"order_type": "all"}, {"order_type": "2"}):
        r = await client.post("/merchant-api/order/list", json=body, headers=headers)
        assert r.json()["data"]["total"] == 0, body

    # 不入余额/流水
    await session.refresh(merchant)
    assert merchant.balance == 0
    records = (await session.exec(select(MerchantRecord).where(
        MerchantRecord.order_trade_no == trade_no))).all()
    assert records == []

    # 管理端订单列表能看到这笔测试单（挂在系统测试商户名下）
    admin_headers = await _admin_headers(session)
    r = await client.post("/admin-api/order/list",
                          json={"order_type": "2", "trade_no": trade_no},
                          headers=admin_headers)
    items = r.json()["data"]["items"]
    assert len(items) == 1 and items[0]["merchant_username"] == "系统测试"


@pytest.mark.asyncio
async def test_merchant_testpay_does_not_consume_channel_limits(client, seeded, session):
    """测试单不占通道日限额：把限笔设成 1，连测两笔后正式下单仍然可用。"""
    channel = seeded["channel"]
    channel.day_limit_count = 1
    session.add(channel)
    await session.commit()

    headers = await _merchant_headers(session)
    for _ in range(2):
        r = await client.post("/merchant-api/testpay/submit",
                              json={"type": "alipay", "amount": "1.00"}, headers=headers)
        assert r.json()["code"] == 0, r.text

    from tests.test_pay_flow import _create_order
    data, order = await _create_order(client, out_trade_no="REAL_AFTER_TEST")
    assert data["code"] == 0, data
    assert order is not None


@pytest.mark.asyncio
async def test_toggle_debug_log_only_touches_that_field(session, client, seeded):
    """列表开关只改 debug_log：费率/限额等未上表单的字段不能被默认值覆盖。"""
    headers = await _admin_headers(session, "chanadmin2")
    channel = seeded["channel"]

    r = await client.put(f"/admin-api/pay/channels/{channel.id}/debug-log",
                         json={"enabled": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["debug_log"] == 1

    await session.refresh(channel)
    assert channel.debug_log == 1
    # 原有配置保持原值
    assert str(channel.merchant_rate) == "2.00"
    assert str(channel.cost_rate) == "1.00"
    assert str(channel.min_amount) == "1.00"

    r = await client.put(f"/admin-api/pay/channels/{channel.id}/debug-log",
                         json={"enabled": False}, headers=headers)
    assert r.status_code == 200, r.text
    await session.refresh(channel)
    assert channel.debug_log == 0


@pytest.mark.asyncio
async def test_plugin_inputs_use_label_and_drop_appswitch(session, client, seeded):
    headers = await _admin_headers(session, "chanadmin3")

    r = await client.get("/admin-api/pay/plugins", headers=headers)
    assert r.status_code == 200
    epay = next(p for p in r.json()["data"] if p["name"] == "epay")
    names = [i["name"] for i in epay["inputs"]]
    assert names == ["appurl", "appid", "appkey"]
    assert all(i.get("label") for i in epay["inputs"])
    assert "appswitch" not in names
    assert epay["supported_types"] == ["alipay", "wxpay"]

    # 详情同样返回 label（通道表单按它渲染）
    channel = (await session.exec(select(Channel))).first()
    r = await client.get(f"/admin-api/pay/channels/{channel.id}",
                         params={"reveal": 1}, headers=headers)
    inputs = r.json()["data"]["inputs"]
    assert [i["label"] for i in inputs] == ["接口地址", "商户ID", "商户密钥"]
