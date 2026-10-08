"""支付主流程端到端测试（全新 REST 接口）：

1. POST /api/orders 签名下单（RSA2，JSON body 内签名）→ 拿到上游 payurl（金额/费率落库正确）
2. 同参重复下单 → 幂等返回同一订单
3. 上游回调 /callbacks/{plugin}/{trade_no}（验签通过）→ 订单已支付 + 商户余额/流水正确
   + 重复回调幂等 + 插件名不一致拒绝
4. 金额不符回调 → 记账拒绝
5. 商户通知失败 → 阶梯重试状态落库
6. 屏蔽回调后主动查单补单 → 记账 + 通知
7. GET /api/orders/{trade_no} 查单（query 签名）
8. 收银台 /cashier/* 与 /cashier 页面
"""
import hashlib
import time
from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from app.models import Merchant, MerchantRecord, NotifyLog, Order
from app.services.balance import dmoney
from app.services.order import get_order_by_trade_no
from tests.conftest import MERCHANT_KEY, UPSTREAM_KEY, signed_params, upstream_state


def _sign_epay(data: dict, key: str) -> str:
    """上游 epay 协议 MD5 签名（上游协议未变，仅回调/查单用）。"""
    items = sorted(
        (k, v) for k, v in data.items()
        if k not in ("sign", "sign_type") and v is not None and v != ""
        and not isinstance(v, (list, dict))
    )
    raw = "&".join(f"{k}={v}" for k, v in items) + key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


async def _fetch_order(trade_no: str):
    """从应用使用的同一测试库读取订单。"""
    from app.core.database import get_db
    from main import app as _app
    override = _app.dependency_overrides[get_db]
    async for s in override():
        return await get_order_by_trade_no(s, trade_no)
    return None


async def _create_order(client, out_trade_no="T1001", money="100.00",
                        sign=None, notify_url="", **extra):
    """走 POST /api/orders 下单（RSA2 签名），返回 (json, 订单|None)。"""
    params = {
        "pid": "1001",
        "type": "alipay",
        "out_trade_no": out_trade_no,
        "notify_url": notify_url or "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品",
        "money": money,
    }
    params.update(extra)
    params = signed_params(params)
    if sign is not None:
        params["sign"] = sign  # 显式坏签名用例

    resp = await client.post("/api/orders", json=params)
    data = resp.json()
    trade_no = (data.get("data") or {}).get("trade_no")
    return data, await _fetch_order(trade_no) if trade_no else None


def _notify_params(order, money="100", trade_no="UPSTREAM999") -> dict:
    params = {
        "pid": "1001",
        "name": "测试商品",
        "money": money,
        "out_trade_no": order.out_trade_no,
        "trade_no": trade_no,
        "type": "alipay",
        "trade_status": "TRADE_SUCCESS",
    }
    params["sign"] = _sign_epay(params, UPSTREAM_KEY)
    params["sign_type"] = "MD5"
    return params


@pytest.mark.asyncio
async def test_create_order_and_notify_paid(client, seeded):
    data, order = await _create_order(client)
    assert data["code"] == 0, data
    assert data["data"]["pay_type"] == "jump"
    assert data["data"]["payurl"] == "https://upstream.example/pay/abc123"
    assert "/cashier/" in data["data"]["cashier_url"]  # 不透明收银台链接
    assert order is not None
    assert order.status == 0
    assert order.realmoney == dmoney("100.00")
    assert order.merchant_income == dmoney("98.00")   # 100 * (100-2%)
    assert order.channel_cost == dmoney("1.00")
    assert order.profit == dmoney("1.00")

    # ---- 上游回调（验签） ----
    resp = await client.post(f"/callbacks/epay/{order.trade_no}",
                             params=_notify_params(order))
    assert resp.text == "success", resp.text

    fresh = await _fetch_order(order.trade_no)
    assert fresh.status == 1
    assert fresh.api_trade_no == "UPSTREAM999"

    from app.core.database import get_db
    from main import app as _app
    override = _app.dependency_overrides[get_db]
    async for s in override():
        m = await s.get(Merchant, fresh.merchant_id)
        assert m.balance == dmoney("98.00")
        records = (await s.exec(select(MerchantRecord).where(
            MerchantRecord.order_trade_no == fresh.trade_no))).all()
        assert len(records) == 1
        assert records[0].amount == dmoney("98.00")

    # ---- 重复回调幂等 ----
    resp2 = await client.post(f"/callbacks/epay/{order.trade_no}",
                              params=_notify_params(order))
    assert resp2.text == "success"
    async for s in override():
        records = (await s.exec(select(MerchantRecord).where(
            MerchantRecord.order_trade_no == fresh.trade_no))).all()
        assert len(records) == 1  # 不重复入账


@pytest.mark.asyncio
async def test_create_order_idempotent(client, seeded):
    data1, order1 = await _create_order(client)
    data2, order2 = await _create_order(client)
    assert data1["code"] == 0 and data2["code"] == 0
    assert order1.trade_no == order2.trade_no


@pytest.mark.asyncio
async def test_idempotent_resubmit_keeps_pay_info_and_realmoney(client, seeded, session):
    """幂等重提交返回落库支付信息，不重新拉起；随机金额单 realmoney 不再重随机。

    回归：曾出现同 out_trade_no 重复下单重新随机 realmoney，导致首次返回的
    付款链接金额与订单实付不符、回调金额比对失败。
    """
    from app.models import Merchant as _M

    m = await session.get(_M, seeded["merchant"].id)
    m.random_enable = True
    m.random_min = dmoney("5")
    m.random_max = dmoney("5")   # 固定 5，便于断言不被重随机成其它值
    session.add(m)
    await session.commit()

    data1, order1 = await _create_order(client, out_trade_no="IDEM-RAND")
    assert data1["code"] == 0
    assert order1.realmoney == dmoney("5")

    # 已拉起过：重复提交幂等返回同一支付信息（不重复请求上游）
    o = await session.get(Order, order1.id)
    o.extra = {**(o.extra or {}), "pay_info": "https://upstream.example/pay/MARKED"}
    session.add(o)
    await session.commit()

    data2, order2 = await _create_order(client, out_trade_no="IDEM-RAND")
    assert data2["code"] == 0
    assert data2["data"]["trade_no"] == order1.trade_no
    assert data2["data"]["payurl"] == "https://upstream.example/pay/MARKED"
    assert order2.realmoney == dmoney("5")


@pytest.mark.asyncio
async def test_trade_no_collision_retry(client, seeded, monkeypatch):
    """trade_no 撞号：唯一键冲突后自动换号重试成功（回归：压测 ~370单/s 撞出 500）。"""
    import itertools as _it

    import app.services.order as order_mod

    data1, order1 = await _create_order(client, out_trade_no="COLL-1")
    assert data1["code"] == 0
    assert len(order1.trade_no) == 22  # 微秒时间戳(18)+4随机

    real_gen = order_mod.generate_trade_no
    calls = _it.count()

    def fake_gen():
        # 前两次返回已占用单号制造撞号（初始构造 + 第一次重试），之后恢复真实生成
        return order1.trade_no if next(calls) < 2 else real_gen()

    monkeypatch.setattr(order_mod, "generate_trade_no", fake_gen)

    data2, order2 = await _create_order(client, out_trade_no="COLL-2")
    assert data2["code"] == 0, data2
    assert order2.trade_no != order1.trade_no
    assert len(order2.trade_no) == 22


@pytest.mark.asyncio
async def test_create_order_bad_sign(client, seeded):
    data, order = await _create_order(client, sign="deadbeef")
    assert data["code"] != 0
    assert order is None


@pytest.mark.asyncio
async def test_callback_plugin_mismatch_rejected(client, seeded):
    """URL 插件名与订单通道插件不符 → 拒绝（防伪造探测）。"""
    data, order = await _create_order(client)
    assert data["code"] == 0
    resp = await client.post(f"/callbacks/wrongplugin/{order.trade_no}",
                             params=_notify_params(order))
    assert resp.status_code == 400
    fresh = await _fetch_order(order.trade_no)
    assert fresh.status == 0


@pytest.mark.asyncio
async def test_notify_amount_mismatch(client, seeded):
    data, order = await _create_order(client)
    assert data["code"] == 0

    params = _notify_params(order, money="99.99", trade_no="UPSTREAM888")
    resp = await client.post(f"/callbacks/epay/{order.trade_no}", params=params)
    assert resp.text == "fail"

    fresh = await _fetch_order(order.trade_no)
    assert fresh.status == 0  # 未记账


@pytest.mark.asyncio
async def test_notify_ladder_retry_state(client, seeded):
    """通知失败 → notify_count 递增、next_notify_at 按阶梯推进。"""
    from app.services.notify import notify_merchant_once

    data, order = await _create_order(
        client, notify_url="http://127.0.0.1:9/never")  # 必然连接失败
    assert data["code"] == 0

    # 记账（模拟回调成功）
    resp = await client.post(f"/callbacks/epay/{order.trade_no}",
                             params=_notify_params(order, trade_no="UP777"))
    assert resp.text == "success"

    from app.core.database import get_db
    from main import app as _app
    override = _app.dependency_overrides[get_db]
    async for s in override():
        # 直接调用投递服务（等价 worker 执行 notify_merchant_job）
        ok = await notify_merchant_once(s, trade_no=order.trade_no)
        assert ok is False

        fresh = await get_order_by_trade_no(s, order.trade_no)
        assert fresh.status == 1
        assert fresh.notify_count == 1
        assert fresh.notify_status == 1  # 待重试
        assert fresh.next_notify_at is not None
        first_delay = fresh.next_notify_at - datetime.now()
        assert timedelta(minutes=0.5) < first_delay < timedelta(minutes=2.5)

        logs = (await s.exec(select(NotifyLog).where(
            NotifyLog.trade_no == order.trade_no))).all()
        assert len(logs) == 1
        assert logs[0].success is False
        assert logs[0].attempt == 1

        # 第二次投递失败 → 阶梯第二档（约 2 分钟）
        ok2 = await notify_merchant_once(s, trade_no=order.trade_no)
        assert ok2 is False
        fresh2 = await get_order_by_trade_no(s, order.trade_no)
        assert fresh2.notify_count == 2
        second_delay = fresh2.next_notify_at - datetime.now()
        assert timedelta(minutes=1.5) < second_delay < timedelta(minutes=3.5)


@pytest.mark.asyncio
async def test_query_upstream_fill_order(engine, client, seeded):
    """屏蔽回调场景：主动查单发现已支付 → 补记账。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession
    import app.worker.jobs as jobs_mod

    data, order = await _create_order(client)
    assert data["code"] == 0
    assert order.status == 0

    upstream_state["query_status"] = 1  # 上游已支付

    from app.core.database import get_db
    from main import app as _app
    # 直接复用 fixture 的 engine（同一测试库），否则写入/读取落到不同文件
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    real = jobs_mod.session_maker
    jobs_mod.session_maker = maker
    try:
        await jobs_mod.query_upstream_job(None, order.trade_no)
    finally:
        jobs_mod.session_maker = real

    fresh = await _fetch_order(order.trade_no)
    assert fresh.status == 1, f"订单未补单: status={fresh.status}"
    override = _app.dependency_overrides[get_db]
    async for s in override():
        records = (await s.exec(select(MerchantRecord).where(
            MerchantRecord.order_trade_no == fresh.trade_no))).all()
        assert len(records) == 1


@pytest.mark.asyncio
async def test_query_order_api(client, seeded):
    data, order = await _create_order(client)
    assert data["code"] == 0

    params = signed_params({"pid": "1001"})
    resp = await client.get(f"/api/orders/{order.trade_no}", params=params)
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["status"] == 0
    assert body["data"]["refund_status"] == "NONE"
    assert body["data"]["refunded_amount"] == "0"
    assert body["data"]["out_trade_no"] == "T1001"
    assert body["data"]["money"] == "100"  # 去尾零

    # 错误签名 → 非 0 code
    bad = signed_params({"pid": "1001"})
    bad["sign"] = "wrong"
    resp2 = await client.get(f"/api/orders/{order.trade_no}", params=bad)
    assert resp2.status_code in (400, 401)
    assert resp2.json()["code"] != 0


@pytest.mark.asyncio
async def test_cashier_endpoints(client, seeded):
    data, order = await _create_order(client)
    assert data["code"] == 0

    resp = await client.get("/cashier/types", params={"trade_no": order.trade_no})
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["status"] == 0
    assert any(t["code"] == "alipay" for t in body["data"]["types"])
    # 下单接口已拉起支付（jump）→ types 附带 pay_type/pay_info 供收银台自动跳转
    assert body["data"]["pay_type"] == "jump"
    assert body["data"]["pay_info"] == "https://upstream.example/pay/abc123"

    resp2 = await client.get("/cashier/status", params={"trade_no": order.trade_no})
    assert resp2.json()["data"]["status"] == 0

    # 收银台切换支付方式再拉起
    resp3 = await client.post("/cashier/pay", json={
        "trade_no": order.trade_no, "type": "alipay"})
    body3 = resp3.json()
    assert body3["code"] == 0, body3
    assert body3["data"]["pay_type"] == "jump"


@pytest.mark.asyncio
async def test_cashier_page_html(client, seeded):
    from app.lib.linktoken import decode_link_token, encode_link_token
    data, order = await _create_order(client, "CASH-PAGE")
    token = encode_link_token(order.trade_no)
    assert decode_link_token(token) == order.trade_no
    resp = await client.get(f"/cashier/{token}")
    assert resp.status_code == 200
    assert "收银台" in resp.text
    # 无效令牌 → 错误结果页（不是收银台）
    resp_bad = await client.get("/cashier/whatever-token")
    assert "链接无效" in resp_bad.text
