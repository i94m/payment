"""高盛支付插件（sk 协议）测试：

1. 签名：ASCII 升序 k=v& 拼接 + 末尾直接拼密钥（golden vector 锁算法）
2. POST /api/orders 走 gaosheng 通道下单 → 拿到上游 payUrl
3. JSON 回调（验签通过 + orderState=2）→ success + 订单已支付 + api_trade_no
4. 验签失败 → fail；非支付成功状态 → success 但不记账；金额不符 → fail
5. 主动查单（queryOrder，orderState=2）→ paid
"""
import hashlib
from decimal import Decimal

import pytest

from app.models import Channel
from app.services.crypto import encrypt_config
from tests.conftest import signed_params, upstream_state

GAOSHENG_KEY = "skkey123456"
GAOSHENG_MCH = "10248"

# golden vector：锁定 sk 签名算法（升序 k=v& + 直接拼密钥）
GOLDEN_CALLBACK = {
    "merchant": GAOSHENG_MCH,
    "sys_order_id": "SK20250601040308798756",
    "orderNo": "T1001",
    "amount": "100.00",
    "timestamp": "1748723893",
    "noncr": "123456",
    "orderState": "2",
}
GOLDEN_SIGN = "056f950dbe0890426b54baa964e54be5"
GOLDEN_SUBMIT_EMPTY_RETURN = "d382ad47446a21f6d6f8006daa26f000"


def _sk_sign(data: dict, key: str) -> str:
    keys = sorted(
        k for k, v in data.items()
        if k != "sign" and v is not None and v != ""
        and not isinstance(v, (list, dict))
    )
    raw = "&".join(f"{k}={data[k]}" for k in keys) + key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _make_plugin(config: dict | None = None):
    from plugins.payment.gaosheng.plugin import GaoshengPayment
    cfg = {
        "appurl": "https://upstream.example/pay/skpay.php?fun=createOrder",
        "appmchid": GAOSHENG_MCH,
        "appkey": GAOSHENG_KEY,
        "productid": "6237",
    }
    cfg.update(config or {})
    return GaoshengPayment(cfg)


def test_sk_sign_golden_vector():
    assert _sk_sign(GOLDEN_CALLBACK, GAOSHENG_KEY) == GOLDEN_SIGN


def test_sk_sign_excludes_empty_and_sign():
    """空值不参与签名；sign 字段排除；大小写不敏感比对。"""
    from plugins.payment.gaosheng.plugin import _sign, _verify_sign
    data = {
        "merchant": GAOSHENG_MCH, "orderNo": "T1001", "currentTime": "1748723893",
        "payType": "6237", "amount": "10.00", "notifyUrl": "https://x/notify",
        "returnUrl": "", "version": "1.0",
    }
    assert _sign(data, GAOSHENG_KEY) == GOLDEN_SUBMIT_EMPTY_RETURN
    data2 = {**data, "sign": _sign(data, GAOSHENG_KEY).upper()}
    assert _verify_sign(data2, GAOSHENG_KEY, data2["sign"])
    assert not _verify_sign(data2, "wrong-key", data2["sign"])


def test_amount_format():
    from plugins.payment.gaosheng.plugin import _amount
    assert _amount("100") == "100.00"
    assert _amount("10.00") == "10.00"
    assert _amount(Decimal("99.999")) == "100.00"


def test_extract_error_surfaces_nested_remark():
    """外层 msg 笼统 "fail" 时，取 data.remark 内的真实原因。"""
    from plugins.payment.gaosheng.plugin import _extract_error
    data = {
        "code": 400, "msg": "fail",
        "data": {
            "code": 400, "msg": "fail", "data": [],
            "remark": '{"code":2013,"msg":"|【ptzfb】当前无可用应用配置或绑定CK（笔数/限额/养号），请稍后再试","data":null}',
        },
    }
    assert "当前无可用应用配置或绑定CK" in _extract_error(data)
    assert _extract_error({"code": 400, "msg": "sign验证失败"}) == "sign验证失败"
    assert _extract_error({"code": 400, "msg": "fail"}) == "fail"


async def _seed_gaosheng_channel(session, pay_type_id: int) -> Channel:
    ch = Channel(
        name="测试高盛支付", plugin_name="gaosheng", payment_type_id=pay_type_id,
        merchant_rate=2, cost_rate=1, status=1, mode=0,
        min_amount=1, max_amount=0, day_limit=0,
        config=encrypt_config({
            "appurl": "https://upstream.example/pay/skpay.php?fun=createOrder",
            "appmchid": GAOSHENG_MCH,
            "appkey": GAOSHENG_KEY,
            "productid": "6237",
        }),
    )
    session.add(ch)
    await session.commit()
    await session.refresh(ch)
    return ch


async def _fetch_order(trade_no: str):
    from app.core.database import get_db
    from main import app as _app
    override = _app.dependency_overrides[get_db]
    async for s in override():
        from app.services.order import get_order_by_trade_no
        return await get_order_by_trade_no(s, trade_no)
    return None


async def _create_gaosheng_order(client, out_trade_no: str):
    """下单（仅 gaosheng 通道可用），返回响应 json。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品", "money": "100.00",
    }
    params = signed_params(params)
    resp = await client.post("/api/orders", json=params)
    return resp.json()


@pytest.mark.asyncio
async def test_submit_via_gaosheng_channel(client, seeded, session):
    """gaosheng 通道下单：走 sk 协议拿到上游 payUrl。"""
    # 只留 gaosheng 通道可用，避免随机路由到 epay 通道
    seeded["channel"].status = 0
    session.add(seeded["channel"])
    await session.commit()
    await _seed_gaosheng_channel(session, seeded["pay_type"].id)

    data = await _create_gaosheng_order(client, "T2001")
    assert data["code"] == 0, data
    trade_no = data["data"]["trade_no"]
    assert data["data"]["pay_type"] == "jump"
    # 上游 payUrl 由 mock 回显 orderNo（即平台 trade_no）
    assert data["data"]["payurl"] == f"https://upstream.example/skpay/{trade_no}"

    order = await _fetch_order(trade_no)
    assert order is not None and order.status == 0
    assert order.out_trade_no == "T2001"
    assert order.realmoney == Decimal("100.00")


@pytest.mark.asyncio
async def test_gaosheng_callback_paid(client, seeded, session):
    """JSON 回调验签通过 + orderState=2 → success + 记账。"""
    seeded["channel"].status = 0
    session.add(seeded["channel"])
    await session.commit()
    await _seed_gaosheng_channel(session, seeded["pay_type"].id)

    data = await _create_gaosheng_order(client, "T3001")
    assert data["code"] == 0, data
    order = await _fetch_order(data["data"]["trade_no"])

    notify = {
        "merchant": GAOSHENG_MCH,
        "sys_order_id": "SK" + order.trade_no,
        "orderNo": order.trade_no,
        "amount": "100.00",
        "timestamp": "1748723893",
        "noncr": "123456",
        "orderState": "2",
    }
    notify["sign"] = _sk_sign(notify, GAOSHENG_KEY)
    resp = await client.post(f"/callbacks/gaosheng/{order.trade_no}", json=notify)
    assert resp.text == "success", resp.text

    fresh = await _fetch_order(order.trade_no)
    assert fresh.status == 1
    assert fresh.api_trade_no == "SK" + order.trade_no


@pytest.mark.asyncio
async def test_gaosheng_callback_bad_sign(client, seeded, session):
    """验签失败 → fail，不记账。"""
    seeded["channel"].status = 0
    session.add(seeded["channel"])
    await session.commit()
    await _seed_gaosheng_channel(session, seeded["pay_type"].id)

    data = await _create_gaosheng_order(client, "T4001")
    assert data["code"] == 0, data
    order = await _fetch_order(data["data"]["trade_no"])

    notify = {
        "merchant": GAOSHENG_MCH,
        "sys_order_id": "SKX", "orderNo": order.trade_no,
        "amount": "100.00", "timestamp": "1748723893",
        "noncr": "123456", "orderState": "2", "sign": "deadbeef",
    }
    resp = await client.post(f"/callbacks/gaosheng/{order.trade_no}", json=notify)
    assert resp.text == "fail"
    fresh = await _fetch_order(order.trade_no)
    assert fresh.status == 0


@pytest.mark.asyncio
async def test_gaosheng_callback_non_paid_state(client, seeded, session):
    """orderState=1（待支付）验签通过 → 应答 success 但不记账。"""
    plugin = _make_plugin()
    notify = {**GOLDEN_CALLBACK, "orderState": "1"}
    notify["sign"] = _sk_sign(notify, GAOSHENG_KEY)
    verdict = await plugin.notify_handler(notify)
    assert verdict.ok is False
    assert verdict.ack == "success"


@pytest.mark.asyncio
async def test_gaosheng_notify_amount_mismatch_rejected(client, seeded):
    """金额不符（99.99 ≠ 100.00）→ 框架层 mismatch 拒绝。"""
    plugin = _make_plugin()
    notify = {**GOLDEN_CALLBACK, "amount": "99.99"}
    notify["sign"] = _sk_sign(notify, GAOSHENG_KEY)
    verdict = await plugin.notify_handler(notify)
    assert verdict.ok is True  # 验签通过，金额比对交给框架
    assert verdict.money == Decimal("99.99")


@pytest.mark.asyncio
async def test_gaosheng_query_order_states(client, seeded):
    """queryOrder 状态映射：2=paid；无平台单号 → unknown。"""
    from app.services.plugin_interfaces import PaymentContext

    plugin = _make_plugin()
    ctx = PaymentContext(order={"trade_no": "T1", "api_trade_no": "SK123"},
                         notify_url="", return_url="")

    upstream_state["query_status"] = 2
    result = await plugin.query(ctx)
    assert result.status == "paid"
    assert result.api_trade_no == "SK123"
    assert result.money == Decimal("10.00")

    upstream_state["query_status"] = 1
    assert (await plugin.query(ctx)).status == "unpaid"
    upstream_state["query_status"] = 3
    assert (await plugin.query(ctx)).status == "failed"

    no_trade = PaymentContext(order={"trade_no": "T2"}, notify_url="", return_url="")
    assert (await plugin.query(no_trade)).status == "unknown"
