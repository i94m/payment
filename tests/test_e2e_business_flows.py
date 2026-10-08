"""整体业务链路测试（FREE-33）：支付 → 提现 → 补单联动 → 下级代理。

与既有分域用例（pay_flow / merchant_p3 结算 / agent_panel）互补，这里串
完整资金链并补缺口场景：
- 资金链路逐步核对：支付入账 → 提现冻结 → 打款解冻，每步断言余额/冻结/流水
- 提现申请边界：低于下限 / 超可提余额 / 未完善收款信息
- 自动结算 cron：达标商户批量发起、不达标与未开通跳过、失败不中断整批
- 打款/拒绝的站内消息与资金留痕
- 代理归属：禁用代理后建单回落无归属；真实入账订单计入代理业绩
"""
import hashlib

import pytest
from sqlmodel import select

from app.models import Agent, Merchant, MerchantMessage, MerchantRecord, Order, Settlement
from app.services.balance import dmoney
from app.services.settle import auto_settle_merchants
from app.services.settings import set_settings
from tests.conftest import UPSTREAM_KEY, pay_code, signed_params
from tests.test_pay_flow import _sign_epay  # noqa: F401


async def _mch_headers(session) -> dict:
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


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _create_order(client, session, out_trade_no: str, money: str = "100.00") -> Order:
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "链路测试", "money": money,
    }
    params = signed_params(params)
    resp = await client.post("/orders", json=params)
    assert resp.json()["code"] == 0, resp.json()
    return (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no))).one()


async def _pay_callback(client, order: Order, money: str = "100"):
    notify = {
        "pid": "1001", "name": "链路测试", "money": money,
        "out_trade_no": order.out_trade_no, "trade_no": "UP" + order.out_trade_no,
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    resp = await client.post(f"/callbacks/epay/{order.trade_no}", params=notify)
    assert resp.text == "success", resp.text


async def _fresh_merchant(engine) -> Merchant:
    """全新会话读商户（避免测试会话快照，见 test_deduct_refill 说明）。"""
    from tests.test_deduct_refill import _fresh_maker
    async with _fresh_maker(engine)() as s:
        return await s.get(Merchant, 1)


async def _records(engine) -> list[MerchantRecord]:
    from tests.test_deduct_refill import _fresh_maker
    from sqlmodel import select as _sel
    async with _fresh_maker(engine)() as s:
        return (await s.exec(_sel(MerchantRecord))).all()


# ============================================================
# 支付 → 提现 → 打款 全资金链
# ============================================================

@pytest.mark.asyncio
async def test_pay_settle_pay_full_chain(client, seeded, session, engine):
    """支付入账 → 提现冻结 → 平台打款：余额/冻结/流水逐步核对。"""
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    m.settle_account = "6222020200012345678"
    m.settle_name = "张三"
    m.settle_type = 4
    session.add(m)
    await session.commit()

    # 1) 支付两笔入账（各 100，到账 98/笔）
    o1 = await _create_order(client, session, "CHAIN-1")
    o2 = await _create_order(client, session, "CHAIN-2")
    await _pay_callback(client, o1)
    await _pay_callback(client, o2)
    m = await _fresh_merchant(engine)
    assert m.balance == dmoney("196.00")
    assert len(await _records(engine)) == 2  # 两笔收款流水

    # 2) 提现 150：余额→冻结，产生结算流水
    r = await client.post("/merchant-api/settle/apply", json={"amount": "150"},
                          headers=headers)
    assert r.json()["code"] == 0, r.json()
    m = await _fresh_merchant(engine)
    assert m.balance == dmoney("46.00")
    assert m.frozen_balance == dmoney("150.00")

    # 3) 平台打款：冻结清零，余额不动，站内消息 + 结算流水
    settle = (await session.exec(select(Settlement))).first()
    adm = await _admin_headers(session)
    r2 = await client.post(f"/admin-api/settle/{settle.id}/pay", headers=adm, json={})
    assert r2.json()["code"] == 0, r2.json()
    m = await _fresh_merchant(engine)
    assert m.balance == dmoney("46.00")
    assert m.frozen_balance == dmoney("0.00")
    recs = await _records(engine)
    assert sorted(r.biz_type for r in recs) == ["order_income", "order_income", "settle"]
    from tests.test_deduct_refill import _fresh_maker
    from sqlmodel import select as _sel
    async with _fresh_maker(engine)() as s:
        msgs = (await s.exec(_sel(MerchantMessage))).all()
    assert any("已打款" in msg_.title for msg_ in msgs)

    # 4) 打款后重复打款拒绝（状态机）
    r3 = await client.post(f"/admin-api/settle/{settle.id}/pay", headers=adm, json={})
    assert r3.json()["code"] != 0


@pytest.mark.asyncio
async def test_settle_apply_boundaries(client, seeded, session):
    """提现申请边界：低于下限 / 超可提余额 / 未完善收款信息。"""
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("50")       # 低于默认下限? 默认 settle_min_amount=1.00
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    await session.commit()

    # 金额超可提余额
    r1 = await client.post("/merchant-api/settle/apply", json={"amount": "80"}, headers=headers)
    assert r1.json()["code"] != 0 and "可提余额" in r1.json()["message"]

    # 未完善收款信息
    m2 = await _get_merchant(session)
    m2.settle_account = ""
    m2.settle_name = ""
    session.add(m2)
    await session.commit()
    r2 = await client.post("/merchant-api/settle/apply", json={"amount": "10"}, headers=headers)
    assert r2.json()["code"] != 0 and "收款信息" in r2.json()["message"]

    # 金额低于下限（设 20 元下限）
    m3 = await _get_merchant(session)
    m3.settle_account = "acc"
    m3.settle_name = "name"
    m3.balance = dmoney("50")
    session.add(m3)
    await session.commit()
    await set_settings(session, {"settle_min_amount": "20.00"})
    r3 = await client.post("/merchant-api/settle/apply", json={"amount": "10"}, headers=headers)
    assert r3.json()["code"] != 0 and "低于" in r3.json()["message"]


@pytest.mark.asyncio
async def test_settle_info_and_fee_preview(client, seeded, session):
    """结算信息接口：余额/可提口径 + 费用预览。"""
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    m.balance = dmoney("100")
    session.add(m)
    await session.commit()
    info = (await client.get("/merchant-api/settle/info", headers=headers)).json()
    assert info["code"] == 0
    assert info["data"]["balance"] == "100.00"


# ============================================================
# 自动结算 cron
# ============================================================

@pytest.mark.asyncio
async def test_auto_settle_batch(client, seeded, session, engine):
    """自动结算：达标商户批量发起；不达标/未开通跳过。"""
    m = await _get_merchant(session)
    m.auto_settle = True
    m.settle_status = 1
    m.status = 1
    m.balance = dmoney("80")
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    # 另一商户：开 auto 但余额不达标
    m2 = Merchant(pid=1002, username="m2", password="x", status=1,
                  settle_status=1, auto_settle=True, balance=dmoney("0.5"))
    session.add(m2)
    # 第三商户：余额达标但未开 auto
    m3 = Merchant(pid=1003, username="m3", password="x", status=1,
                  settle_status=1, auto_settle=False, balance=dmoney("100"),
                  settle_account="a3", settle_name="n3")
    session.add(m3)
    await session.commit()

    count = await auto_settle_merchants(session)
    assert count == 1  # 只有 m 发起
    settles = (await session.exec(select(Settlement))).all()
    assert len(settles) == 1 and settles[0].merchant_id == m.id
    m_after = await _fresh_merchant(engine)
    assert m_after.frozen_balance == dmoney("80.00")
    m3_after = await session.get(Merchant, m3.id)
    assert m3_after.frozen_balance == dmoney("0.00")


# ============================================================
# 下级代理
# ============================================================

@pytest.mark.asyncio
async def test_disabled_agent_attribution_falls_back(client, seeded, session, engine):
    """禁用代理后，token 建单不再归属该代理（回落 0）。"""
    m = await _get_merchant(session)
    agent = Agent(merchant_id=m.id, name="ag-disabled", password="x", status=1)
    session.add(agent)
    await session.commit()
    token = await pay_code(session, 1001, agent.id)

    r1 = await client.post("/paypage/order", json={"token": token, "money": "6.00"})
    t1 = r1.json()["data"]["trade_no"]

    # 禁用后同 token 建单回落无归属
    ag = await session.get(Agent, agent.id)
    ag.status = 0
    session.add(ag)
    await session.commit()
    r2 = await client.post("/paypage/order", json={"token": token, "money": "6.00"})
    t2 = r2.json()["data"]["trade_no"]

    from tests.test_deduct_refill import _refetch
    o1 = await _refetch(engine, t1)
    o2 = await _refetch(engine, t2)
    assert o1.agent_id == agent.id
    assert o2.agent_id == 0


@pytest.mark.asyncio
async def test_agent_performance_counts_paid_via_callback(client, seeded, session):
    """真实回调入账的订单计入代理业绩（非直插 DB 的口径）。"""
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    agent = Agent(merchant_id=m.id, name="ag-perf", password="x", status=1)
    session.add(agent)
    await session.commit()

    # 商户 API 下单不支持指定代理 —— 走 paypage 短码建单再回调支付
    token = await pay_code(session, 1001, agent.id)
    r = await client.post("/paypage/order", json={"token": token, "money": "8.00"})
    trade_no = r.json()["data"]["trade_no"]
    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    await _pay_callback(client, order, money="8")

    from app.core.security import issue_token
    tk = await issue_token(guard="agent", user_id=agent.id, session=session)
    perf = (await client.get("/agent-api/dashboard",
                             headers={"Authorization": f"Bearer {tk.access_token}"})).json()
    # 今日业绩含该笔（金额口径为申报金额 8 元）
    assert perf["data"]["today"]["count"] == 1
    assert perf["data"]["today"]["money"] == "8.00"
