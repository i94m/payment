"""商户后台 P3 批次端到端测试：

- 注册：开关/审核/邀请码绑定/密码规则
- 结算：冻结口径（申请冻结→打款扣冻结/拒绝退回）、手续费保底封顶、每日上限、D+1
- 结算方式结构化（settle_type 下发与保存）
- 测试支付：测试商户挂单返回收银台地址
- 扣量：频次命中、商户侧遮蔽（列表/详情/查单/统计）、admin 回补入账
- 通知失败联动：重试耗尽达阈值自动关闭支付权限 + 站内消息
"""
import pytest
from sqlmodel import select

from app.models import Merchant, MerchantMessage, Order, Settlement, SystemAdmin
from app.services.balance import dmoney
from app.services.settings import set_settings
from tests.conftest import UPSTREAM_KEY, signed_params
from tests.test_pay_flow import _sign_epay  # noqa: F401


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _mch_headers(session) -> dict:
    m = await _get_merchant(session)
    from app.core.security import issue_token
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _admin_headers(session) -> dict:
    admin = SystemAdmin(username="rootadmin3", name="root", password="x", is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    from app.core.security import issue_token
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _pay_order(client, session, out_trade_no, money="100.00"):
    """走开放 API 下单并模拟上游回调支付成功（与 p0p2 相同路径）。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品", "money": money,
    }
    params = signed_params(params)
    resp = await client.post("/api/orders", json=params)
    assert resp.json()["code"] == 0, resp.json()
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
# 注册
# ============================================================

@pytest.mark.asyncio
async def test_register_disabled_by_default(client, seeded, session):
    r = await client.post("/merchant-api/auth/register", json={
        "username": "newbie", "password": "abc123",
        "captcha_key": "x", "captcha_code": "0000",
    })
    assert r.json()["code"] != 0
    assert "未开放" in r.json()["message"]


@pytest.mark.asyncio
async def test_reg_switch_hidden_from_settings_page(client, seeded, session):
    """注册开关不在站点配置页展示（页面白名单收敛），但配置功能保留：PATCH 仍可调、注册照常受控。"""
    h = await _admin_headers(session)
    resp = await client.get("/admin-api/setting", headers=h)
    keys = {i["key"] for i in resp.json()["data"]["items"]}
    assert "merchant_reg_open" not in keys

    # 写入走同一校验通道（页面隐藏 ≠ 只读）
    resp2 = await client.patch("/admin-api/setting", json={"merchant_reg_open": True}, headers=h)
    assert resp2.json()["code"] == 0

    # 打开后注册入口照常生效
    r = await client.post("/merchant-api/auth/register", json={
        "username": "regopen", "password": "abc123",
        "captcha_key": "x", "captcha_code": "0000",
    })
    assert r.json()["code"] == 0, r.json()


@pytest.mark.asyncio
async def test_register_flow_with_review_and_invite(client, seeded, session):
    from app.services.settings import set_settings as _ss
    await _ss(session, {"merchant_reg_open": True, "merchant_reg_review": True})

    inviter = Merchant(pid=1009, username="inviter", password="x",
                       status=1, invite_code="invite123")
    session.add(inviter)
    await session.commit()
    await session.refresh(inviter)
    inviter_id = inviter.id

    # 密码规则：纯数字拒绝
    r = await client.post("/merchant-api/auth/register", json={
        "username": "newbie", "password": "123456",
        "captcha_key": "x", "captcha_code": "0000",
    })
    assert r.json()["code"] != 0

    r2 = await client.post("/merchant-api/auth/register", json={
        "username": "newbie", "password": "abc123", "email": "new@ex.com",
        "invite_code": "invite123",
        "captcha_key": "x", "captcha_code": "0000",
    })
    body = r2.json()
    assert body["code"] == 0, body
    assert body["data"]["pay_status"] == 2  # 待审核

    session.expire_all()
    m = (await session.exec(select(Merchant).where(Merchant.username == "newbie"))).one()
    assert m.pay_status == 2
    assert m.invited_by == inviter_id
    assert m.invite_code and len(m.invite_code) == 16
    assert m.settle_type == 1


# ============================================================
# 结算：冻结口径 / 保底封顶 / 每日上限 / D+1
# ============================================================

@pytest.mark.asyncio
async def test_settle_freeze_flow(client, seeded, session):
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("100")
    m.settle_account = "acc"
    m.settle_name = "name"
    m.settle_type = 4
    session.add(m)
    await session.commit()

    r = await client.post("/merchant-api/settle/apply", json={"amount": "60"}, headers=headers)
    assert r.json()["code"] == 0, r.json()
    session.expire_all()
    await session.refresh(m)
    assert m.balance == dmoney("40")
    assert m.frozen_balance == dmoney("60")

    settle = (await session.exec(select(Settlement).where(Settlement.merchant_id == m.id))).first()
    assert settle.status == 0
    assert settle.settle_type == 4  # 结算单记录结构化方式

    # 管理端打款 → 冻结扣减
    adm = await _admin_headers(session)
    r2 = await client.post(f"/admin-api/settle/{settle.id}/pay", headers=adm, json={})
    assert r2.json()["code"] == 0
    session.expire_all()
    await session.refresh(m)
    assert m.balance == dmoney("40")
    assert m.frozen_balance == dmoney("0")


@pytest.mark.asyncio
async def test_settle_reject_returns_frozen_to_balance(client, seeded, session):
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("100")
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    await session.commit()

    await client.post("/merchant-api/settle/apply", json={"amount": "60"}, headers=headers)
    settle = (await session.exec(select(Settlement).where(Settlement.merchant_id == m.id))).first()

    adm = await _admin_headers(session)
    r = await client.post(f"/admin-api/settle/{settle.id}/reject", headers=adm,
                          json={"remark": "信息有误"})
    assert r.json()["code"] == 0
    session.expire_all()
    await session.refresh(m)
    assert m.balance == dmoney("100")
    assert m.frozen_balance == dmoney("0")


@pytest.mark.asyncio
async def test_settle_fee_min_max_and_daily_limit(client, seeded, session):
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("1000")
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    await set_settings(session, {"settle_fee_rate": "1.00", "settle_fee_min": "2.00",
                                 "settle_daily_limit": 1})
    session.add(m)
    await session.commit()

    # 10 元 × 1% = 0.1 → 保底 2 元
    r = await client.post("/merchant-api/settle/apply", json={"amount": "10"}, headers=headers)
    assert r.json()["code"] == 0
    settle = (await session.exec(select(Settlement).where(Settlement.merchant_id == m.id))).first()
    assert settle.fee == dmoney("2.00")
    assert settle.real_amount == dmoney("8.00")

    # 每日 1 次上限
    r2 = await client.post("/merchant-api/settle/apply", json={"amount": "10"}, headers=headers)
    assert r2.json()["code"] != 0
    assert "上限" in r2.json()["message"]

    await set_settings(session, {"settle_fee_rate": "1.00", "settle_fee_min": "0.00",
                                 "settle_daily_limit": 0})


@pytest.mark.asyncio
async def test_settle_d1_blocks_today_income(client, seeded, session):
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    await set_settings(session, {"settle_d1_enable": True})
    await session.commit()

    order = await _pay_order(client, session, "D1A")  # 到账 98
    assert order.status == 1
    session.expire_all()
    await session.refresh(m)
    assert m.balance == dmoney("98")

    # 当日收入不可提：余额 98 全部为当日收入
    r = await client.post("/merchant-api/settle/apply", json={"amount": "50"}, headers=headers)
    assert r.json()["code"] != 0
    assert "可提" in r.json()["message"]

    info = (await client.get("/merchant-api/settle/info", headers=headers)).json()["data"]
    assert info["d1_enable"] is True
    assert info["today_income"] == "98.00"
    assert info["withdrawable"] == "0.00"

    await set_settings(session, {"settle_d1_enable": False})


@pytest.mark.asyncio
async def test_settle_type_in_info_and_profile(client, seeded, session):
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.settle_type = 2
    session.add(m)
    await session.commit()

    info = (await client.get("/merchant-api/settle/info", headers=headers)).json()["data"]
    assert info["settle_type"] == 2
    assert info["settle_type_name"] == "微信"

    r = await client.put("/merchant-api/account/profile", headers=headers, json={
        "settle_account": "acc2", "settle_name": "name2", "settle_type": 4})
    assert r.json()["code"] == 0
    session.expire_all()
    await session.refresh(m)
    assert m.settle_type == 4


# ============================================================
# 扣量（默认关闭；开启后频次轮询、商户侧遮蔽、admin 回补）
# ============================================================

@pytest.mark.asyncio
async def test_deduct_disabled_by_default(client, seeded, session):
    order = await _pay_order(client, session, "DED0")
    assert order.status == 1
    assert order.is_deducted is False


@pytest.mark.asyncio
async def test_deduct_hit_masks_and_undo(client, seeded, session):
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 2})

    m = await _get_merchant(session)
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()

    o1 = await _pay_order(client, session, "DD1")  # 第 1 笔：正常
    o2 = await _pay_order(client, session, "DD2")  # 第 2 笔：命中扣量
    # 第二次 commit 使 o1 过期，重查取新实例
    o1 = (await session.exec(select(Order).where(Order.out_trade_no == "DD1"))).one()
    o2 = (await session.exec(select(Order).where(Order.out_trade_no == "DD2"))).one()
    o1_trade, o2_trade = o1.trade_no, o2.trade_no  # 后续 commit 会过期实例，先取单号
    assert o1.is_deducted is False
    assert o2.is_deducted is True
    assert o2.status == 1  # 平台侧已支付
    assert o2.notify_status == 0  # 不通知

    session.expire_all()
    await session.refresh(m)
    # 仅第 1 笔入账 98 元
    assert m.balance == dmoney("98")

    # 商户列表：扣量单显示为未支付、无完成时间
    headers = await _mch_headers(session)
    lst = (await client.post("/merchant-api/order/list", headers=headers, json={
        "trade_no": o2_trade})).json()["data"]["items"]
    assert lst[0]["status"] == 0
    assert lst[0]["paid_at"] == ""
    assert lst[0]["merchant_income"] == ""

    # 状态筛选：已支付视图不含扣量单；未支付视图含
    paid = (await client.post("/merchant-api/order/list", headers=headers, json={
        "status": 1})).json()["data"]
    assert all(i["trade_no"] != o2_trade for i in paid["items"])
    unpaid = (await client.post("/merchant-api/order/list", headers=headers, json={
        "status": 0})).json()["data"]
    assert any(i["trade_no"] == o2_trade for i in unpaid["items"])
    # 统计口径不含扣量单
    assert paid["summary"]["count"] == 1

    # 商户重发通知被拒
    r = await client.post(f"/merchant-api/order/{o2_trade}/notify", headers=headers)
    assert r.json()["code"] != 0

    # admin 回补：入账 + 通知排队 + 标记清除
    adm = await _admin_headers(session)
    r2 = await client.post("/admin-api/order/refill", headers=adm,
                           json={"trade_no": o2_trade})
    assert r2.json()["code"] == 0, r2.json()
    session.expire_all()
    await session.refresh(o2)
    await session.refresh(m)
    assert o2.is_deducted is False
    assert o2.notify_status == 1  # 已排队补发
    assert m.balance == dmoney("196")

    await set_settings(session, {"deduct_enable": False, "deduct_frequency": 0})


class _FakeRedis:
    """进程内假 Redis：INCR 原子语义 + 分布式锁/设置缓存用到的最小接口"""

    def __init__(self):
        self.store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None, **kwargs):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]


@pytest.mark.asyncio
async def test_deduct_counter_redis_atomic(client, seeded, session):
    """Redis INCR 原子计数：频次 3，前 6 笔命中第 3/6 笔，模循环无需重置。

    覆盖有 Redis 时的主路径（默认 fixture 是 DisabledCache，走行锁回退分支）。
    """
    from app.core.cache import get_cache
    from main import app

    fake = _FakeRedis()
    orig = app.dependency_overrides.get(get_cache)

    async def _fake_cache():
        yield fake

    app.dependency_overrides[get_cache] = _fake_cache
    try:
        await set_settings(session, {"deduct_enable": True, "deduct_frequency": 3})
        hits = []
        for i in range(1, 7):
            o = await _pay_order(client, session, f"RC{i}")
            hits.append(o.is_deducted)
        assert hits == [False, False, True, False, False, True]
        # 计数器未被重置：第 6 笔命中后继续累计（第 9 笔再命中）
        o7 = await _pay_order(client, session, "RC7")
        o8 = await _pay_order(client, session, "RC8")
        o9 = await _pay_order(client, session, "RC9")
        assert [o7.is_deducted, o8.is_deducted, o9.is_deducted] == [False, False, True]
    finally:
        if orig is not None:
            app.dependency_overrides[get_cache] = orig
        else:
            app.dependency_overrides.pop(get_cache, None)
        await set_settings(session, {"deduct_enable": False, "deduct_frequency": 0})


# ============================================================
# 通知失败联动：重试耗尽达阈值 → 自动关支付权限
# ============================================================

@pytest.mark.asyncio
async def test_notify_fail_blocks_pay_status(client, seeded, session):
    from app.services.notify import notify_merchant_once

    await set_settings(session, {
        "notify_fail_block_enable": True,
        "notify_fail_block_threshold": 1,
        "notify_max_attempts": 1,
        "notify_retry_ladder": [1],
    })

    order = await _pay_order(client, session, "NFB1")
    assert order.status == 1
    assert order.notify_status == 1

    ok = await notify_merchant_once(session, trade_no=order.trade_no)
    assert ok is False
    session.expire_all()
    await session.refresh(order)
    assert order.notify_status == -1  # 重试耗尽

    m = await _get_merchant(session)
    session.expire_all()
    await session.refresh(m)
    assert m.pay_status == 0  # 自动关闭支付权限

    msg = (await session.exec(
        select(MerchantMessage).where(MerchantMessage.merchant_id == m.id,
                                      MerchantMessage.title == "支付权限已被关闭")
    )).first()
    assert msg is not None

    # 支付权限关闭后无法再建单
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "NFB2",
        "notify_url": "https://merchant.example/notify", "return_url": "",
        "name": "测试商品", "money": "1.00",
    }
    params = signed_params(params)
    r = await client.post("/api/orders", json=params)
    assert r.json()["code"] != 0

    await set_settings(session, {
        "notify_fail_block_enable": False,
        "notify_fail_block_threshold": 10,
        "notify_max_attempts": 6,
        "notify_retry_ladder": [1, 2, 5, 10, 30, 60, 120],
    })
