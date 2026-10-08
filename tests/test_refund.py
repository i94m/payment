"""商户退款 API 测试（同步退款模型）：

- 全额 / 部分多次 / 超额拒 / 幂等重放 / 余额不足拒 / 未支付拒 / 扣量单拒
- 退满置 order.status=2 / 流水 biz_type=refund / 查单退款字段 / 开关关闭拒
- 上游失败零副作用（不落库、不扣余额）
"""
import pytest
from sqlmodel import select

from app.models import Merchant, MerchantRecord, Order, Refund
from app.services.balance import dmoney
from app.services.settings import set_settings
from tests.conftest import UPSTREAM_KEY, signed_params, upstream_state
from tests.test_pay_flow import _sign_epay


async def _pay_order(client, session, out_trade_no, money="100.00") -> Order:
    """API 下单 + 上游回调支付成功（100 → merchant_income 98）。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "退款测试", "money": money,
    }
    params = signed_params(params)
    resp = await client.post("/orders", json=params)
    assert resp.json()["code"] == 0, resp.json()
    order = (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no))).one()
    notify = {
        "pid": "1001", "name": "退款测试", "money": "100",
        "out_trade_no": order.out_trade_no, "trade_no": "UP" + out_trade_no,
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    resp2 = await client.post(f"/callbacks/epay/{order.trade_no}", params=notify)
    assert resp2.status_code == 200
    session.expire_all()
    order = (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no))).one()
    assert order.status == 1
    return order


async def _refund(client, trade_no, amount=None, out_refund_no=None, reason=None):
    params = {"pid": "1001"}
    if amount is not None:
        params["refund_amount"] = amount
    if out_refund_no is not None:
        params["out_refund_no"] = out_refund_no
    if reason is not None:
        params["reason"] = reason
    resp = await client.post(f"/orders/{trade_no}/refund", json=signed_params(params))
    return resp.json()


async def _refunds_of(session, trade_no) -> list[Refund]:
    return (await session.exec(select(Refund).where(Refund.trade_no == trade_no))).all()


async def _merchant_balance(session) -> dmoney:
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    return dmoney(m.balance)


@pytest.mark.asyncio
async def test_full_refund_default_amount(client, seeded, session):
    """缺省金额 = 剩余可退全额（merchant_income 98）；退满置 order.status=2。"""
    order = await _pay_order(client, session, "RF-FULL")
    assert await _merchant_balance(session) == dmoney("98.00")

    body = await _refund(client, order.trade_no)
    assert body["code"] == 0, body
    assert body["data"]["amount"] == "98.00"
    assert body["data"]["status"] == 1
    assert body["data"]["refund_no"].startswith("R") and len(body["data"]["refund_no"]) == 22

    order_id = order.id  # expire 后实例属性访问会触发同步懒加载，先取主键
    session.expire_all()
    fresh = await session.get(Order, order_id)
    assert fresh.status == 2  # 已退款
    assert await _merchant_balance(session) == dmoney("0.00")
    refunds = await _refunds_of(session, order.trade_no)
    assert len(refunds) == 1 and refunds[0].amount == dmoney("98.00")


@pytest.mark.asyncio
async def test_partial_refunds_multiple_times(client, seeded, session):
    """多次部分退款：余额按次扣减，订单保持已支付，PARTIAL → FULL。"""
    order = await _pay_order(client, session, "RF-PART")
    order_id = order.id  # expire 后实例属性访问会触发同步懒加载，先取主键

    b1 = await _refund(client, order.trade_no, amount="30.00", out_refund_no="R001")
    assert b1["code"] == 0, b1
    assert await _merchant_balance(session) == dmoney("68.00")
    session.expire_all()
    assert (await session.get(Order, order_id)).status == 1

    b2 = await _refund(client, order.trade_no, amount="68", out_refund_no="R002")
    assert b2["code"] == 0, b2
    session.expire_all()
    assert (await session.get(Order, order_id)).status == 2  # 退满
    assert await _merchant_balance(session) == dmoney("0.00")
    assert len(await _refunds_of(session, order.trade_no)) == 2


@pytest.mark.asyncio
async def test_exceed_refundable_rejected(client, seeded, session):
    """超出可退上限（98）拒：不落库不扣款。"""
    order = await _pay_order(client, session, "RF-EXCEED")

    body = await _refund(client, order.trade_no, amount="98.01")
    assert body["code"] != 0
    assert "可退" in body["message"]
    assert await _refunds_of(session, order.trade_no) == []
    assert await _merchant_balance(session) == dmoney("98.00")


@pytest.mark.asyncio
async def test_out_refund_no_idempotent(client, seeded, session):
    """同 out_refund_no 幂等：重复提交返回原单，只扣一次款。"""
    order = await _pay_order(client, session, "RF-IDEM")

    b1 = await _refund(client, order.trade_no, amount="10.00", out_refund_no="IDEM1")
    b2 = await _refund(client, order.trade_no, amount="10.00", out_refund_no="IDEM1")
    assert b1["code"] == 0 and b2["code"] == 0
    assert b1["data"]["refund_no"] == b2["data"]["refund_no"]

    assert len(await _refunds_of(session, order.trade_no)) == 1
    assert await _merchant_balance(session) == dmoney("88.00")


@pytest.mark.asyncio
async def test_insufficient_balance_rejected(client, seeded, session):
    """余额 < 退款额拒（订单已支付但余额被结算走光）。"""
    order = await _pay_order(client, session, "RF-NOBAL")

    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    m.balance = dmoney("1.00")
    session.add(m)
    await session.commit()

    body = await _refund(client, order.trade_no, amount="50.00")
    assert body["code"] != 0
    assert "余额不足" in body["message"]
    assert await _refunds_of(session, order.trade_no) == []


@pytest.mark.asyncio
async def test_unpaid_order_rejected(client, seeded, session):
    """未支付订单拒退。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "RF-UNPAID",
        "notify_url": "https://merchant.example/notify", "return_url": "",
        "name": "退款测试", "money": "10.00",
    }
    resp = await client.post("/orders", json=signed_params(params))
    trade_no = resp.json()["data"]["trade_no"]

    body = await _refund(client, trade_no)
    assert body["code"] != 0
    assert "未支付" in body["message"]


@pytest.mark.asyncio
async def test_deducted_order_rejected(client, seeded, session):
    """扣量单拒退（对外"未支付"口径不变）。"""
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 1})

    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "RF-DED",
        "notify_url": "https://merchant.example/notify", "return_url": "",
        "name": "退款测试", "money": "10.00",
    }
    resp = await client.post("/orders", json=signed_params(params))
    trade_no = resp.json()["data"]["trade_no"]
    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    notify = {
        "pid": "1001", "name": "退款测试", "money": "10",
        "out_trade_no": order.out_trade_no, "trade_no": "UPRF-DED",
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    resp2 = await client.post(f"/callbacks/epay/{trade_no}", params=notify)
    assert resp2.status_code == 200
    session.expire_all()
    order = (await session.exec(select(Order).where(Order.trade_no == trade_no))).one()
    assert order.is_deducted is True and order.status == 1

    body = await _refund(client, trade_no)
    assert body["code"] != 0
    assert "未支付" in body["message"]

    await set_settings(session, {"deduct_enable": False, "deduct_frequency": 0})


@pytest.mark.asyncio
async def test_refund_record_biz_type(client, seeded, session):
    """退款流水：biz_type=refund、负金额、带 trade_no 关联。"""
    order = await _pay_order(client, session, "RF-REC")

    await _refund(client, order.trade_no, amount="20.00", reason="test", out_refund_no="REC1")

    records = (await session.exec(select(MerchantRecord).where(
        MerchantRecord.order_trade_no == order.trade_no))).all()
    # order_income(+98) + refund(-20)
    assert len(records) == 2
    refund_rec = next(r for r in records if r.biz_type == "refund")
    assert refund_rec.amount == dmoney("-20.00")
    assert refund_rec.balance_before == dmoney("98.00")
    assert refund_rec.balance_after == dmoney("78.00")


@pytest.mark.asyncio
async def test_query_refund_and_order_fields(client, seeded, session):
    """退款单查询（refund_no + out_refund_no 兜底）与查单退款字段。"""
    order = await _pay_order(client, session, "RF-QUERY")

    b1 = await _refund(client, order.trade_no, amount="40.00", out_refund_no="QRY1")
    refund_no = b1["data"]["refund_no"]

    # refund_no 查询
    q = await client.get(f"/refunds/{refund_no}",
                         params=signed_params({"pid": "1001"}))
    assert q.json()["code"] == 0
    assert q.json()["data"]["out_refund_no"] == "QRY1"
    assert q.json()["data"]["amount"] == "40.00"
    assert q.json()["data"]["status"] == 1
    assert q.json()["data"]["reason"] == ""

    # out_refund_no 兜底查询（refund_no 随便给）
    q2 = await client.get("/refunds/none",
                          params=signed_params({"pid": "1001", "out_refund_no": "QRY1"}))
    assert q2.json()["code"] == 0
    assert q2.json()["data"]["refund_no"] == refund_no

    # 不存在的退款单
    q3 = await client.get("/refunds/R0000000000000000000099",
                          params=signed_params({"pid": "1001"}))
    assert q3.json()["code"] != 0

    # 查单退款字段：部分退
    oq = await client.get(f"/orders/{order.trade_no}",
                          params=signed_params({"pid": "1001"}))
    assert oq.json()["code"] == 0
    assert oq.json()["data"]["refund_status"] == "PARTIAL"
    assert oq.json()["data"]["refunded_amount"] == "40"

    # 退满后 → FULL
    await _refund(client, order.trade_no, amount="58.00")
    oq2 = await client.get(f"/orders/{order.trade_no}",
                           params=signed_params({"pid": "1001"}))
    assert oq2.json()["data"]["refund_status"] == "FULL"
    assert oq2.json()["data"]["refunded_amount"] == "98"


@pytest.mark.asyncio
async def test_refund_switch_off_rejected(client, seeded, session):
    """api_refund_enable=False：退款接口拒绝。"""
    order = await _pay_order(client, session, "RF-SWITCH")
    await set_settings(session, {"api_refund_enable": False})

    body = await _refund(client, order.trade_no)
    assert body["code"] != 0
    assert "退款接口未开放" in body["message"]
    assert await _refunds_of(session, order.trade_no) == []

    await set_settings(session, {"api_refund_enable": True})


@pytest.mark.asyncio
async def test_upstream_fail_zero_side_effect(client, seeded, session):
    """上游退款失败：报错不落库、不扣余额（零副作用）。"""
    order = await _pay_order(client, session, "RF-UPFAIL")
    order_id = order.id  # expire 后实例属性访问会触发同步懒加载，先取主键
    upstream_state["refund_ok"] = False
    try:
        body = await _refund(client, order.trade_no)
        assert body["code"] != 0
        assert await _refunds_of(session, order.trade_no) == []
        assert await _merchant_balance(session) == dmoney("98.00")
        session.expire_all()
        assert (await session.get(Order, order_id)).status == 1
    finally:
        upstream_state["refund_ok"] = True
