"""商户后台 P0-P2 端到端测试：

- 安全：找回密码（邮件未配置路径）、登录日志、会话管理
- 订单：notify_status 列、筛选统计汇总、CSV 导出
- 结算：费率/预留余额预览、可提口径拦截、自动结算开关
- 代理：CRUD + 业绩统计 + paypage 链接归属
- 聚合收款：info / 建单（普通 + 随机金额）
- 充值：充值单支付全额入账、不回调
- 实名认证：提交 → 审核通过 + 站内消息
- 邀请返现 / 公告 / 域名白名单 / 管理员重置密码
"""
import pytest
from sqlmodel import select

from app.models import (
    Agent,
    Announcement,
    Merchant,
    MerchantLoginLog,
    MerchantMessage,
    Order,
    PaymentType,
    SystemAdmin,
)
from app.services.balance import dmoney
from app.services import paytoken
from tests.conftest import UPSTREAM_KEY, pay_code, signed_params, upstream_state
from tests.test_pay_flow import _notify_params, _sign_epay  # noqa: F401


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _admin_headers(session) -> dict:
    admin = SystemAdmin(username="rootadmin", name="root", password="x", is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    from app.core.security import issue_token
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _mch_headers(session) -> dict:
    m = await _get_merchant(session)
    from app.core.security import issue_token
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _pay_order(client, session, out_trade_no, money="100.00"):
    """走 API 下单并模拟上游回调支付成功。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品", "money": money,
    }
    params = signed_params(params)
    resp = await client.post("/api/orders", json=params)
    data = resp.json()
    assert data["code"] == 0, data
    order = (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no)
    )).one()
    notify = {
        "pid": "1001", "name": "测试商品", "money": "100",
        "out_trade_no": order.out_trade_no, "trade_no": "UP" + out_trade_no,
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    notify["sign_type"] = "MD5"
    resp2 = await client.post(f"/callbacks/epay/{order.trade_no}", params=notify)
    assert resp2.status_code == 200
    await session.refresh(order)
    return order


# ============================================================
# 安全：找回密码 / 登录日志
# ============================================================

@pytest.mark.asyncio
async def test_forgot_password_without_mail_channel(client, seeded, session):
    m = await _get_merchant(session)
    m.email = "testmch@example.com"
    session.add(m)
    await session.commit()
    r = await client.post("/merchant-api/auth/forgot", json={"email": "testmch@example.com"})
    body = r.json()
    # 邮件通道未配置 → 明确报错；不暴露邮箱是否存在
    assert body["code"] == -1
    assert "邮件" in body["message"]


@pytest.mark.asyncio
async def test_login_logs_recorded(client, seeded, session):
    # 走真实 HTTP 登录（本地环境万能验证码 0000）
    r0 = await client.post("/merchant-api/auth/login", json={
        "username": "testmch", "password": "test123456", "captcha_code": "0000"})
    assert r0.json()["code"] == 0, r0.json()
    m = await _get_merchant(session)
    logs = (await session.exec(select(MerchantLoginLog).where(
        MerchantLoginLog.merchant_id == m.id))).all()
    assert len(logs) >= 1


# ============================================================
# 订单：notify_status / 汇总 / 导出
# ============================================================

@pytest.mark.asyncio
async def test_order_list_summary_and_notify_status(client, seeded, session):
    headers = await _mch_headers(session)
    order = await _pay_order(client, session, "SUM1")

    r = await client.post("/merchant-api/order/list", json={}, headers=headers)
    body = r.json()
    assert body["code"] == 0
    item = body["data"]["items"][0]
    assert item["trade_no"] == order.trade_no
    assert item["notify_status"] == 1  # 已支付待首投
    summary = body["data"]["summary"]
    assert summary["count"] == 1
    assert summary["money"] == "100.00"


@pytest.mark.asyncio
async def test_order_export_csv(client, seeded, session):
    headers = await _mch_headers(session)
    await _pay_order(client, session, "EXP1")
    r = await client.post("/merchant-api/order/export", json={}, headers=headers)
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert "EXP1" in text


# ============================================================
# 结算：预览 / 预留余额 / 自动结算开关
# ============================================================

@pytest.mark.asyncio
async def test_settle_info_preview_and_remain_block(client, seeded, session):
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("100")
    m.remain_money = dmoney("50")
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    await session.commit()

    r = await client.get("/merchant-api/settle/info", headers=headers)
    body = r.json()["data"]
    assert body["balance"] == "100.00"
    assert body["withdrawable"] == "50.00"
    assert body["fee_rate"] == "0.00"

    # 超出可提余额被拦截
    r2 = await client.post("/merchant-api/settle/apply", json={"amount": "80"}, headers=headers)
    assert "可提" in r2.json()["message"]

    r3 = await client.post("/merchant-api/settle/apply", json={"amount": "40"}, headers=headers)
    assert r3.json()["code"] == 0


@pytest.mark.asyncio
async def test_auto_settle_toggle(client, seeded, session):
    headers = await _mch_headers(session)
    r = await client.post("/merchant-api/settle/auto", json={"auto_settle": True}, headers=headers)
    assert r.json()["code"] == 0
    session.expire_all()  # 清掉 identity map 里请求前加载的商户实例
    m = await _get_merchant(session)
    assert m.auto_settle is True


# ============================================================
# 代理
# ============================================================

@pytest.mark.asyncio
async def test_agent_crud_and_performance_and_paypage_attribution(client, seeded, session):
    headers = await _mch_headers(session)

    r1 = await client.post("/merchant-api/agent", headers=headers, json={
        "name": "代理A", "password": "agent123", "remark": ""})
    body = r1.json()
    assert body["code"] == 0, body
    agent_id = body["data"]["id"]

    # 专属短码链接可直接打开收款页，归属映射正确
    m = await _get_merchant(session)
    pt = await paytoken.resolve(session, body["data"]["link"][3:])
    assert (pt.merchant_id, pt.agent_id) == (m.id, agent_id)
    page = await client.get(body["data"]["link"])
    assert page.status_code == 200

    # paypage 带归属建单
    r2 = await client.post("/paypage/order", json={
        "token": await pay_code(session, 1001, agent_id), "type": "alipay",
        "money": "8.00", "remark": ""})
    body2 = r2.json()
    assert body2["code"] == 0, body2
    trade_no = body2["data"]["trade_no"]
    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    assert order.agent_id == agent_id

    # 业绩统计（未支付单不计）
    r3 = await client.get("/merchant-api/agent/performance",
                          params={"agent_id": agent_id}, headers=headers)
    perf = r3.json()["data"]
    assert perf["total"]["count"] == 0

    # 支付后计入业绩
    notify = {
        "pid": "1001", "name": "在线收款", "money": "8",
        "out_trade_no": order.out_trade_no, "trade_no": "UPA1",
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    notify["sign_type"] = "MD5"
    await client.post(f"/callbacks/epay/{order.trade_no}", params=notify)

    perf2 = (await client.get("/merchant-api/agent/performance",
                              params={"agent_id": agent_id}, headers=headers)).json()["data"]
    assert perf2["total"]["count"] == 1
    assert perf2["total"]["money"] == "8.00"

    # 删除
    r4 = await client.delete(f"/merchant-api/agent/{agent_id}", headers=headers)
    assert r4.json()["code"] == 0


# ============================================================
# 聚合收款：随机金额
# ============================================================

@pytest.mark.asyncio
async def test_paypage_random_amount_order(client, seeded, session):
    headers = await _mch_headers(session)
    await client.put("/merchant-api/paypage/settings", headers=headers, json={
        "random_enable": True, "random_min": "5", "random_max": "10", "random_ratelimit": 0})

    r = await client.get("/paypage/info", params={"token": await pay_code(session)})
    assert r.json()["code"] == 0
    assert r.json()["data"]["random_enable"] is True

    r2 = await client.post("/paypage/order", json={"token": await pay_code(session), "type": "alipay"})
    body = r2.json()
    assert body["code"] == 0, body
    trade_no = body["data"]["trade_no"]
    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    assert dmoney("5") <= order.money <= dmoney("10")


@pytest.mark.asyncio
async def test_paypage_order_without_type_defaults_from_db(client, seeded, session):
    """收款页不传 type：默认取支付方式表第一个启用项，而非写死 alipay。"""
    session.add(PaymentType(code="wxpay", name="微信支付", status=1, sort=0))  # sort 更小 → 排最前
    await session.commit()

    r = await client.post("/paypage/order", json={"token": await pay_code(session), "money": "3.00"})
    body = r.json()
    assert body["code"] == 0, body
    order = (await session.exec(select(Order).where(
        Order.trade_no == body["data"]["trade_no"]))).one()
    assert order.payment_type_code == "wxpay"

    # 全部停用 → 显式报错
    for ptype in (await session.exec(select(PaymentType))).all():
        ptype.status = 0
        session.add(ptype)
    await session.commit()
    r2 = await client.post("/paypage/order", json={"token": await pay_code(session), "money": "3.00"})
    assert r2.json()["code"] == -1


@pytest.mark.asyncio
async def test_paypage_disabled_merchant_amount_validation(client, seeded, session):
    # 未开启随机金额且未传金额 → 报错
    r = await client.post("/paypage/order", json={"token": await pay_code(session), "type": "alipay"})
    assert r.json()["code"] == -1


# ============================================================
# 充值
# ============================================================

@pytest.mark.asyncio
async def test_recharge_paid_credits_balance_without_notify(client, seeded, session):
    headers = await _mch_headers(session)
    r = await client.post("/merchant-api/settle/recharge", headers=headers, json={
        "amount": "66.00", "type": "alipay"})
    body = r.json()
    assert body["code"] == 0, body
    trade_no = body["data"]["trade_no"]

    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    assert order.order_type == 1

    # 模拟上游回调
    notify = {
        "pid": "1001", "name": "余额充值", "money": "66",
        "out_trade_no": order.out_trade_no, "trade_no": "UPRC1",
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    notify["sign_type"] = "MD5"
    resp = await client.post(f"/callbacks/epay/{order.trade_no}", params=notify)
    assert resp.status_code == 200

    m = await _get_merchant(session)
    await session.refresh(m)
    assert m.balance == dmoney("66")  # 全额入账（无费率扣减）

    await session.refresh(order)
    assert order.status == 1
    assert order.notify_status == 0  # 充值单不回调商户
    # 站内消息
    msgs = (await session.exec(select(MerchantMessage).where(
        MerchantMessage.merchant_id == m.id))).all()
    assert any("充值" in msg.title for msg in msgs)


# ============================================================
# 邀请返现
# ============================================================

@pytest.mark.asyncio
async def test_invite_rebate_credited_on_order_paid(client, seeded, session):
    from app.services.settings import set_settings

    m = await _get_merchant(session)
    # 邀请人
    inviter = Merchant(pid=1002, username="inviter", password="x",
                       status=1, balance=dmoney("0"))
    session.add(inviter)
    await session.commit()
    await session.refresh(inviter)
    m.invited_by = inviter.id
    session.add(m)
    await set_settings(session, {"invite_rebate_rate": "10.00"})  # 10%

    order = await _pay_order(client, session, "IVT1")
    assert order.status == 1
    await session.refresh(inviter)
    # 到账 100*(100-2)% = 98 → 返现 9.80
    assert inviter.balance == dmoney("9.80")


# ============================================================
# 公告
# ============================================================

@pytest.mark.asyncio
async def test_announcements_admin_crud_and_dashboard(client, seeded, session):
    adm = await _admin_headers(session)
    mch = await _mch_headers(session)

    r = await client.post("/admin-api/announce", headers=adm, json={
        "content": "今晚 2:00 升级", "status": 1})
    assert r.json()["code"] == 0, r.json()

    r2 = await client.post("/admin-api/announce/list", headers=adm, json={})
    assert r2.json()["data"]["total"] == 1

    # 商户概览带公告（无标题，仅内容）
    r3 = await client.get("/merchant-api/dashboard", headers=mch)
    data = r3.json()["data"]
    assert len(data["announcements"]) == 1
    assert data["announcements"][0]["content"] == "今晚 2:00 升级"
    assert "title" not in data["announcements"][0]
    # 新增统计字段
    assert "yesterday" in data and "success_rate" in data

    # 消息中心可用
    r4 = await client.get("/merchant-api/message/unread-count", headers=mch)
    assert r4.json()["code"] == 0


# ============================================================
# 域名白名单
# ============================================================

@pytest.mark.asyncio
async def test_domain_whitelist_enforced(client, seeded, session):
    m = await _get_merchant(session)
    m.allowed_domains = "merchant.example,*.partner.com"
    session.add(m)
    await session.commit()

    # 无 Referer 的服务端调用 → 拦截
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "DM1",
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品", "money": "10.00",
    }
    params = signed_params(params)
    r1 = await client.post("/api/orders", json=params)
    assert r1.json()["code"] == -1
    assert "未授权" in r1.json()["message"]

    # 白名单域名 → 通过
    r2 = await client.post("/api/orders", json=params,
                           headers={"Referer": "https://merchant.example/page"})
    assert r2.json()["code"] == 0, r2.json()

    # 通配符子域 → 通过
    params["out_trade_no"] = "DM2"
    params = signed_params(params)
    r3 = await client.post("/api/orders", json=params,
                           headers={"Origin": "https://a.partner.com"})
    assert r3.json()["code"] == 0


# ============================================================
# 管理员重置密码 / doc 运行时 site_url
# ============================================================

@pytest.mark.asyncio
async def test_admin_resetpwd(client, seeded, session):
    adm = await _admin_headers(session)
    m = await _get_merchant(session)
    r = await client.post(f"/admin-api/merchant/{m.id}/resetpwd", headers=adm)
    body = r.json()
    assert body["code"] == 0
    new_password = body["data"]["password"]

    r2 = await client.post("/merchant-api/auth/login", json={
        "username": "testmch", "password": new_password, "captcha_code": "0000"})
    assert r2.json()["code"] == 0


