"""资金链路并发/订单生命周期修复回归（FREE-86，颖 / P0-1..4、P1-12、P2-16/22/23/24/25、P3-28/29）。

覆盖点：
1. 建单幂等快路径的金额/支付方式一致性校验（P2-16）
2. 过期单：建单入口拦截（P2-22）、回调窗口内回补（P0-4）、窗口外拒绝
3. 退款预占：在途金额占住可退额度（并发超退防线）、孤儿预占清理（P0-3）
4. 邀请返现随退款回冲（P2-25）
5. 回调地址 SSRF 拦截 + 不跟随跳转（P1-12）
6. 结算：手续费保底不得吞掉本金（P2-24）、打款/驳回幂等与冻结不足显式报错（P0-1）
7. 过期任务分批（P3-29）、主动查单按 expire_at 口径 + 去重（P0-4/P3-28）
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlmodel import select

from app.models import Merchant, MerchantRecord, NotifyLog, Order, Refund, Settlement
from app.services.balance import dmoney
from app.services.settings import set_settings
from tests.conftest import UPSTREAM_KEY, signed_params, upstream_state
from tests.test_deduct_refill import _admin_headers, _balance, _refetch
from tests.test_pay_flow import _sign_epay


def _order_params(out_trade_no: str, money: str = "100.00", type_code: str = "alipay") -> dict:
    return signed_params({
        "pid": "1001", "type": type_code, "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "回归", "money": money,
    })


async def _create_order(client, session, out_trade_no: str, money: str = "100.00") -> Order:
    resp = await client.post("/api/orders", json=_order_params(out_trade_no, money))
    assert resp.json()["code"] == 0, resp.json()
    return (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no))).one()


async def _callback_paid(client, order: Order, money: str = "100") -> str:
    params = {
        "pid": "1001", "name": "回归", "money": money,
        "out_trade_no": order.out_trade_no, "trade_no": "UP" + order.out_trade_no,
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    params["sign"] = _sign_epay(params, UPSTREAM_KEY)
    resp = await client.post(f"/callbacks/epay/{order.trade_no}", params=params)
    return resp.text


# ============================================================
# 1. 建单幂等一致性（P2-16）
# ============================================================

@pytest.mark.asyncio
async def test_create_order_idempotent_consistency(client, seeded, session):
    """同 out_trade_no 不同金额/支付方式 → 报错，而不是静默返回旧单。"""
    order = await _create_order(client, session, "CONS-1", "100.00")

    same = await client.post("/api/orders", json=_order_params("CONS-1", "100.00"))
    assert same.json()["code"] == 0
    assert same.json()["data"]["trade_no"] == order.trade_no

    diff_money = await client.post("/api/orders", json=_order_params("CONS-1", "50.00"))
    assert diff_money.json()["code"] != 0
    assert "不一致" in diff_money.json()["message"]

    # 支付方式不一致（wxpay 未启用 → 两种错误都算拒绝，但同一 out_trade_no 不能复用）
    diff_type = await client.post("/api/orders",
                                  json=_order_params("CONS-1", "100.00", "wxpay"))
    assert diff_type.json()["code"] != 0


# ============================================================
# 2. 过期单：入口拦截 + 受控回补窗口（P2-22 / P0-4）
# ============================================================

@pytest.mark.asyncio
async def test_expired_order_rejected_at_create(client, seeded, session):
    """已过有效期的待支付单不再返回给商户（状态就地置过期）。"""
    order = await _create_order(client, session, "EXP-GATE")
    order_id = order.id
    await session.exec(update(Order).where(Order.id == order_id)
                       .values(expire_at=datetime.now() - timedelta(minutes=1)))
    await session.commit()

    resp = await client.post("/api/orders", json=_order_params("EXP-GATE"))
    assert resp.json()["code"] != 0
    assert "过期" in resp.json()["message"]

    session.expire_all()
    assert (await session.get(Order, order_id)).status == 3


@pytest.mark.asyncio
async def test_late_pay_backfill_within_window(client, seeded, session, engine):
    """过期单在回补窗口内收到已验签回调 → 正常入账（钱已收到就必须能记账）。"""
    order = await _create_order(client, session, "LATE-1")
    order_id = order.id
    await session.exec(update(Order).where(Order.id == order_id)
                       .values(status=3, expire_at=datetime.now() - timedelta(minutes=30)))
    await session.commit()

    assert await _callback_paid(client, order) == "success"

    fresh = await _refetch(engine, order.trade_no)
    assert fresh.status == 1 and fresh.paid_at is not None
    assert fresh.notify_status == 1  # 排队补发商户通知
    assert await _balance(engine, seeded["merchant"].id) == dmoney("98.00")


@pytest.mark.asyncio
async def test_late_pay_backfill_outside_window_rejected(client, seeded, session, engine):
    """超出回补窗口的历史过期单不再自动入账（只能后台显式回补）。"""
    from app.services.settings import set_settings as _set

    await _set(session, {"late_pay_backfill_hours": 1})
    try:
        order = await _create_order(client, session, "LATE-2")
        order_id = order.id
        await session.exec(update(Order).where(Order.id == order_id)
                           .values(status=3, expire_at=datetime.now() - timedelta(hours=3)))
        await session.commit()

        assert await _callback_paid(client, order) == "fail"
        fresh = await _refetch(engine, order.trade_no)
        assert fresh.status == 3
        assert await _balance(engine, seeded["merchant"].id) == dmoney("0.00")

        # 后台受控回补仍可人工入账
        r = await client.post("/admin-api/order/refill",
                              headers=await _admin_headers(session),
                              json={"trade_no": order.trade_no})
        assert r.json()["code"] == 0, r.json()
        assert await _balance(engine, seeded["merchant"].id) == dmoney("98.00")
    finally:
        await _set(session, {"late_pay_backfill_hours": 24})


# ============================================================
# 3. 退款预占（P0-3）
# ============================================================

async def _pay_order(client, session, out_trade_no: str) -> Order:
    order = await _create_order(client, session, out_trade_no)
    assert await _callback_paid(client, order) == "success"
    # populate_existing：拿记账后的最新值，同时不 expire 测试会话里的其他实例
    return (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no)
        .execution_options(populate_existing=True))).one()


async def _refund(client, trade_no, amount=None, out_refund_no=None):
    params = {"pid": "1001"}
    if amount is not None:
        params["refund_amount"] = amount
    if out_refund_no is not None:
        params["out_refund_no"] = out_refund_no
    resp = await client.post(f"/api/orders/{trade_no}/refund", json=signed_params(params))
    return resp.json()


@pytest.mark.asyncio
async def test_pending_refund_reserves_capacity(client, seeded, session, engine):
    """在途（status=0）退款预占占住可退额度，防止并发部分退款超退。"""
    order = await _pay_order(client, session, "RSV-1")

    # 模拟另一笔正在走上游的退款：预占 50（可退上限 98 → 剩 48）
    session.add(Refund(
        refund_no="R" + datetime.now().strftime("%y%m%d%H%M%S%f") + "001",
        out_refund_no="HELD-1", trade_no=order.trade_no,
        merchant_id=seeded["merchant"].id, amount=dmoney("50.00"),
        status=0, created_at=datetime.now(),
    ))
    await session.commit()

    body = await _refund(client, order.trade_no, amount="60.00")
    assert body["code"] != 0
    assert "可退" in body["message"]
    assert await _balance(engine, seeded["merchant"].id) == dmoney("98.00")

    # 预占额内仍可退
    ok = await _refund(client, order.trade_no, amount="40.00", out_refund_no="RSV-OK")
    assert ok["code"] == 0, ok
    assert await _balance(engine, seeded["merchant"].id) == dmoney("58.00")


@pytest.mark.asyncio
async def test_orphan_refund_reservation_cleaned(client, seeded, session, engine):
    """超时的孤儿预占会被清理，不会永久锁死可退额度。"""
    order = await _pay_order(client, session, "RSV-2")

    session.add(Refund(
        refund_no="R" + datetime.now().strftime("%y%m%d%H%M%S%f") + "002",
        out_refund_no="ORPHAN-1", trade_no=order.trade_no,
        merchant_id=seeded["merchant"].id, amount=dmoney("98.00"),
        status=0, created_at=datetime.now() - timedelta(minutes=10),
    ))
    await session.commit()

    body = await _refund(client, order.trade_no, amount="98.00", out_refund_no="ORPHAN-1")
    assert body["code"] == 0, body
    assert body["data"]["status"] == 1
    assert await _balance(engine, seeded["merchant"].id) == dmoney("0.00")


@pytest.mark.asyncio
async def test_upstream_fail_keeps_no_reservation(client, seeded, session, engine):
    """上游失败不留下预占行（零资金副作用 + 不占额度）。"""
    order = await _pay_order(client, session, "RSV-3")
    upstream_state["refund_ok"] = False
    try:
        body = await _refund(client, order.trade_no, amount="10.00")
        assert body["code"] != 0
    finally:
        upstream_state["refund_ok"] = True

    rows = (await session.exec(select(Refund).where(Refund.trade_no == order.trade_no))).all()
    assert rows == []
    assert await _balance(engine, seeded["merchant"].id) == dmoney("98.00")


# ============================================================
# 4. 邀请返现回冲（P2-25）
# ============================================================

@pytest.mark.asyncio
async def test_refund_claws_back_invite_rebate(client, seeded, session, engine):
    """退款按比例回冲邀请返现，避免「下单→返现→退款」套利。"""
    from app.core.security import hash_password

    await set_settings(session, {"invite_rebate_rate": "10"})
    try:
        inviter = Merchant(pid=1002, username="inviter", password=hash_password("x"), status=1)
        session.add(inviter)
        await session.commit()
        await session.refresh(inviter)

        m = seeded["merchant"]
        m.invited_by = inviter.id
        session.add(m)
        await session.commit()

        order = await _pay_order(client, session, "REB-1")
        # 到账 98 → 返现 9.80
        assert await _balance(engine, inviter.id) == dmoney("9.80")

        body = await _refund(client, order.trade_no, amount="49.00", out_refund_no="REB-R1")
        assert body["code"] == 0, body
        # 回冲 49×10% = 4.90
        assert await _balance(engine, inviter.id) == dmoney("4.90")

        recs = (await session.exec(select(MerchantRecord).where(
            MerchantRecord.merchant_id == inviter.id))).all()
        assert any(r.biz_type == "invite_rebate" and r.amount == dmoney("-4.90") for r in recs)
    finally:
        await set_settings(session, {"invite_rebate_rate": "0"})


# ============================================================
# 5. 回调地址 SSRF（P1-12）
# ============================================================

@pytest.mark.asyncio
async def test_ssrf_guard_rejects_internal_targets():
    from app.lib.url_guard import UnsafeUrlError, assert_public_http_url

    for url in (
        "http://127.0.0.1/notify",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5:8080/x",
        "http://[::1]/x",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://user:pass@example.com/x",
        "http://no-such-host.invalid/x",  # 解析失败 → fail-closed
    ):
        with pytest.raises(UnsafeUrlError):
            await assert_public_http_url(url)


@pytest.mark.asyncio
async def test_notify_blocks_internal_url(engine, seeded, session):
    """投递侧拦截内网地址：留痕 UNSAFE_URL、按阶梯重试、不打到内网。"""
    from app.services.notify import notify_merchant_once

    m = seeded["merchant"]
    now = datetime.now()
    order = Order(
        trade_no="TS" + now.strftime("%y%m%d%H%M%S%f"),
        merchant_id=m.id, out_trade_no="SSRF-1", payment_type_code="alipay",
        subject="ssrf", money=Decimal("1.00"), realmoney=Decimal("1.00"),
        status=1, created_at=now, paid_at=now, expire_at=now + timedelta(minutes=30),
        notify_url="http://127.0.0.1:9/never", notify_status=1, next_notify_at=now,
    )
    session.add(order)
    await session.commit()

    assert await notify_merchant_once(session, trade_no=order.trade_no) is False
    await session.refresh(order)
    assert order.notify_status == 1 and order.notify_count == 1

    logs = (await session.exec(select(NotifyLog).where(
        NotifyLog.trade_no == order.trade_no))).all()
    assert len(logs) == 1 and logs[0].success is False
    assert logs[0].response.startswith("UNSAFE_URL")
    assert logs[0].http_status is None


# ============================================================
# 6. 结算（P0-1 / P2-24）
# ============================================================

async def _settle_ready_merchant(session, balance: str = "100.00") -> Merchant:
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    m.settle_account = "a@example.com"
    m.settle_name = "张三"
    m.settle_status = 1
    m.balance = dmoney(balance)
    session.add(m)
    await session.commit()
    await session.refresh(m)
    return m


@pytest.mark.asyncio
async def test_settle_fee_min_cannot_exceed_amount(client, seeded, session):
    """保底手续费高于结算金额时拒绝申请，绝不允许 real_amount 为负。"""
    from app.services.settle import apply_settlement

    await set_settings(session, {"settle_min_amount": "1.00", "settle_fee_min": "10.00"})
    m = await _settle_ready_merchant(session)
    merchant_id = m.id

    settle, error = await apply_settlement(session, m, "5.00")
    assert settle is None
    assert "手续费" in error

    fresh = await session.get(Merchant, merchant_id)
    assert dmoney(fresh.balance) == dmoney("100.00")
    assert dmoney(fresh.frozen_balance) == dmoney("0.00")
    assert (await session.exec(select(Settlement))).all() == []


@pytest.mark.asyncio
async def test_settle_paid_twice_deducts_frozen_once(client, seeded, session, engine):
    """打款幂等：第二次打款被状态机拒绝，冻结只扣一次。"""
    from app.services.settle import apply_settlement, mark_settlement_paid

    m = await _settle_ready_merchant(session)
    merchant_id = m.id
    settle, error = await apply_settlement(session, m, "50.00")
    assert error == "", error
    settle_id = settle.id

    ok, msg = await mark_settlement_paid(session, settle_id)
    assert ok and msg == ""
    ok2, msg2 = await mark_settlement_paid(session, settle_id)
    assert ok2 is False and "状态" in msg2

    from tests.test_deduct_refill import _fresh_maker
    async with _fresh_maker(engine)() as s:
        fresh = await s.get(Merchant, merchant_id)
        assert dmoney(fresh.balance) == dmoney("50.00")
        assert dmoney(fresh.frozen_balance) == dmoney("0.00")


@pytest.mark.asyncio
async def test_settle_reject_restores_balance_once(client, seeded, session, engine):
    """驳回退回余额；重复驳回归于状态机拒绝，不会重复退款。"""
    from app.services.settle import apply_settlement, reject_settlement

    m = await _settle_ready_merchant(session)
    merchant_id = m.id
    settle, error = await apply_settlement(session, m, "60.00")
    assert error == "", error
    settle_id = settle.id

    ok, _ = await reject_settlement(session, settle_id, remark="信息有误")
    assert ok
    ok2, _ = await reject_settlement(session, settle_id)
    assert ok2 is False

    from tests.test_deduct_refill import _fresh_maker
    async with _fresh_maker(engine)() as s:
        fresh = await s.get(Merchant, merchant_id)
        assert dmoney(fresh.balance) == dmoney("100.00")
        assert dmoney(fresh.frozen_balance) == dmoney("0.00")


@pytest.mark.asyncio
async def test_settle_mark_paid_frozen_mismatch_refuses(client, seeded, session):
    """冻结余额与结算单不符时显式拒绝打款（不再 max(...,0) 静默截断）。"""
    from app.services.settle import apply_settlement, mark_settlement_paid

    m = await _settle_ready_merchant(session)
    merchant_id = m.id
    settle, error = await apply_settlement(session, m, "50.00")
    assert error == "", error
    settle_id = settle.id

    # 人为制造错账：冻结被清空
    await session.exec(update(Merchant).where(Merchant.id == merchant_id)
                       .values(frozen_balance=0))
    await session.commit()

    ok, msg = await mark_settlement_paid(session, settle_id)
    assert ok is False and "冻结" in msg

    fresh = (await session.exec(
        select(Settlement).where(Settlement.id == settle_id)
        .execution_options(populate_existing=True))).one()
    assert fresh.status == 0  # 未打款


@pytest.mark.asyncio
async def test_settle_paid_survives_notify_failure(client, seeded, session, engine, monkeypatch):
    """通知抛异常不回滚已完成的打款：结算单/余额/冻结额都按打款落库。"""
    import app.services.message as message_module
    from app.services.settle import apply_settlement, mark_settlement_paid

    m = await _settle_ready_merchant(session)
    merchant_id = m.id
    settle, error = await apply_settlement(session, m, "50.00")
    assert error == "", error
    settle_id = settle.id

    async def _boom(*args, **kwargs):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr(message_module, "send_merchant_message", _boom)
    ok, msg = await mark_settlement_paid(session, settle_id)
    assert ok is True and msg == ""  # 通知失败不影响资金操作的返回结果

    from tests.test_deduct_refill import _fresh_maker
    async with _fresh_maker(engine)() as s:
        row = (await s.exec(
            select(Settlement).where(Settlement.id == settle_id))).one()
        assert row.status == 1 and row.paid_at is not None
        fresh = await s.get(Merchant, merchant_id)
        assert dmoney(fresh.balance) == dmoney("50.00")
        assert dmoney(fresh.frozen_balance) == dmoney("0.00")
        # 通知行没落库，但资金结果已在（不是整笔回滚，也不是半提交）
        from app.models import MerchantMessage
        assert (await s.exec(select(MerchantMessage).where(
            MerchantMessage.merchant_id == merchant_id))).all() == []


@pytest.mark.asyncio
async def test_settle_reject_survives_notify_failure(client, seeded, session, engine, monkeypatch):
    """通知抛异常不回滚已完成的驳回：冻结退回余额已落库。"""
    import app.services.message as message_module
    from app.services.settle import apply_settlement, reject_settlement

    m = await _settle_ready_merchant(session)
    merchant_id = m.id
    settle, error = await apply_settlement(session, m, "60.00")
    assert error == "", error
    settle_id = settle.id

    async def _boom(*args, **kwargs):
        raise RuntimeError("SMTP down")

    monkeypatch.setattr(message_module, "send_merchant_message", _boom)
    ok, msg = await reject_settlement(session, settle_id, remark="信息有误")
    assert ok is True and msg == ""

    from tests.test_deduct_refill import _fresh_maker
    async with _fresh_maker(engine)() as s:
        row = (await s.exec(
            select(Settlement).where(Settlement.id == settle_id))).one()
        assert row.status == 2
        fresh = await s.get(Merchant, merchant_id)
        assert dmoney(fresh.balance) == dmoney("100.00")
        assert dmoney(fresh.frozen_balance) == dmoney("0.00")


@pytest.mark.asyncio
async def test_settle_notify_runs_after_funds_commit(client, seeded, session, monkeypatch):
    """通知不在资金事务内：资金 commit 先发生，通知在其后才被调用。"""
    import app.services.message as message_module
    from app.services.settle import apply_settlement, mark_settlement_paid

    m = await _settle_ready_merchant(session)
    settle, error = await apply_settlement(session, m, "50.00")
    assert error == "", error
    settle_id = settle.id

    events: list[str] = []
    state: dict[str, object] = {}

    async def _spy_send(sess, merchant, **kwargs):
        events.append("notify")
        # 打点时点断言：通知被调用时资金事务已经结束（没有开着的事务）
        state["in_transaction"] = sess.in_transaction()

    real_commit = session.commit

    async def _spy_commit():
        events.append("commit")
        await real_commit()

    monkeypatch.setattr(message_module, "send_merchant_message", _spy_send)
    monkeypatch.setattr(session, "commit", _spy_commit)

    ok, msg = await mark_settlement_paid(session, settle_id)
    assert ok and msg == ""
    # 顺序断言：先 commit 冻结扣减/状态机，再把通知投递出去
    assert events[0] == "commit"
    assert events.index("notify") == 1
    assert state["in_transaction"] is False


@pytest.mark.asyncio
async def test_auto_settle_respects_d1_income(client, seeded, session):
    """自动结算的可提口径必须扣 D+1 当日收款（P2-23），否则 D+1 商户永远被拒。"""
    from app.services.settle import auto_settle_merchants

    await set_settings(session, {"settle_d1_enable": True})
    try:
        m = await _settle_ready_merchant(session, balance="98.00")
        merchant_id = m.id
        m = await session.get(Merchant, merchant_id)
        m.auto_settle = True
        session.add(m)

        now = datetime.now()
        order = Order(
            trade_no="TD" + now.strftime("%y%m%d%H%M%S%f"),
            merchant_id=merchant_id, out_trade_no="D1-1", payment_type_code="alipay",
            subject="d1", money=Decimal("100.00"), realmoney=Decimal("100.00"),
            merchant_income=Decimal("98.00"), status=1,
            created_at=now, paid_at=now, expire_at=now + timedelta(minutes=30),
        )
        session.add(order)
        await session.commit()

        # 当日收款不可提 → 不发起自动结算
        assert await auto_settle_merchants(session) == 0
        assert (await session.exec(select(Settlement))).all() == []

        # 移到昨天 → 当日收款归零，可提 98 → 自动结算全额
        await session.exec(update(Order).where(Order.trade_no == order.trade_no)
                           .values(paid_at=now - timedelta(days=1)))
        await session.commit()
        assert await auto_settle_merchants(session) == 1

        settles = (await session.exec(select(Settlement).where(
            Settlement.merchant_id == merchant_id))).all()
        assert len(settles) == 1
        assert dmoney(settles[0].amount) == dmoney("98.00")
        assert dmoney(settles[0].real_amount) == dmoney("98.00")
    finally:
        await set_settings(session, {"settle_d1_enable": False})


# ============================================================
# 8. 开放 API 签名重放（P0-3 ③）
# ============================================================

@pytest.mark.asyncio
async def test_api_signature_single_use():
    """同一签名 300s 内只受理一次（Redis SET NX 语义）。"""
    from app.logic.api_verify import ensure_not_replayed

    class _Nx:
        def __init__(self):
            self.d = {}

        async def set(self, key, value, nx=False, ex=None, **kw):
            if nx and key in self.d:
                return None
            self.d[key] = value
            return True

    cache = _Nx()
    params = {"pid": "1001", "sign": "c2lnbmF0dXJl", "timestamp": "1"}
    await ensure_not_replayed(cache, 1001, params)

    from app.logic.api_verify import ensure_not_replayed as again
    with pytest.raises(ValueError):
        await again(cache, 1001, params)

    # 不同商户/不同签名不受影响
    await again(cache, 1002, params)
    await again(cache, 1001, {**params, "sign": "b3RoZXI="})


# ============================================================
# 9. 过期任务分批 / 主动查单口径与去重（P3-29 / P3-28）
# ============================================================

async def _mk_pending(session, merchant_id: int, channel_id: int, trade_no: str,
                      created: datetime, expire: datetime, status: int = 0) -> Order:
    order = Order(
        trade_no=trade_no, merchant_id=merchant_id, out_trade_no="O" + trade_no,
        payment_type_code="alipay", subject="s", money=Decimal("10.00"),
        realmoney=Decimal("10.00"), status=status, channel_id=channel_id,
        created_at=created, expire_at=expire,
    )
    session.add(order)
    return order


class _FakeCache:
    def __init__(self):
        self.d = {}

    async def get(self, key):
        return self.d.get(key)

    async def set(self, key, value, nx=False, ex=None, **kw):
        if nx and key in self.d:
            return None
        self.d[key] = value
        return True

    async def delete(self, key):
        self.d.pop(key, None)


@pytest.mark.asyncio
async def test_expire_task_batches_and_skips_paid(engine, session, seeded, monkeypatch):
    """过期任务分批执行，且不误伤并发已支付的订单。"""
    import app.worker.jobs as jobs_mod

    now = datetime.now()
    past = now - timedelta(minutes=5)
    for i in range(5):
        await _mk_pending(session, seeded["merchant"].id, seeded["channel"].id,
                          f"EXP-B{i}", created=past, expire=past)
    await _mk_pending(session, seeded["merchant"].id, seeded["channel"].id,
                      "EXP-PAID", created=past, expire=past, status=1)
    await session.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(jobs_mod, "session_maker", maker)
    monkeypatch.setattr(jobs_mod, "EXPIRE_BATCH", 2)

    await jobs_mod.expire_orders_task({"job_id": "cron"})

    async with maker() as s:
        rows = (await s.exec(select(Order))).all()
    by_no = {r.trade_no: r.status for r in rows}
    assert [by_no[f"EXP-B{i}"] for i in range(5)] == [3] * 5
    assert by_no["EXP-PAID"] == 1


@pytest.mark.asyncio
async def test_scan_unpaid_uses_expire_window(engine, session, seeded, monkeypatch):
    """有效期内（即便创建超过窗口分钟）的待支付单必须被查单；早已过期的跳过；同单去重。"""
    import app.core.cache as cache_module
    import app.worker as worker_module
    import app.worker.jobs as jobs_mod
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(jobs_mod, "session_maker", maker)

    fake_cache = _FakeCache()

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield fake_cache

    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)

    class _Queue:
        def __init__(self):
            self.calls = []

        async def enqueue(self, name, **kwargs):
            self.calls.append((name, kwargs))

    fake_queue = _Queue()
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    now = datetime.now()
    # 创建于 25 分钟前、仍在有效期（expire +5 分钟）：旧口径（近 10 分钟创建）会漏掉
    await _mk_pending(session, seeded["merchant"].id, seeded["channel"].id,
                      "SCAN-VALID", created=now - timedelta(minutes=25),
                      expire=now + timedelta(minutes=5))
    # 创建 60 分钟前、已过期 30 分钟：超出宽限窗口
    await _mk_pending(session, seeded["merchant"].id, seeded["channel"].id,
                      "SCAN-OLD", created=now - timedelta(minutes=60),
                      expire=now - timedelta(minutes=30))
    await session.commit()

    await jobs_mod.scan_unpaid_orders({"job_id": "cron"})
    trades = [kw["trade_no"] for _, kw in fake_queue.calls]
    assert "SCAN-VALID" in trades
    assert "SCAN-OLD" not in trades

    # 去重：下一轮同一订单不再重复投递
    fake_queue.calls.clear()
    await jobs_mod.scan_unpaid_orders({"job_id": "cron"})
    assert [kw["trade_no"] for _, kw in fake_queue.calls] == []
