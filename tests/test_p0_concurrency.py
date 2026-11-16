"""P0 资金并发/幂等回归测试（FREE-86 review P0-1/2/3/4 验收门禁）。

覆盖五个口径（对应 review 原文）：
- P0-1 结算双击打款：同一结算单被并发打款两次，冻结额只允许扣一次
- P0-1 打款与驳回并发：两条路径互斥，不允许「既打款又退余额」
- P0-2 并发结算申请：可提余额（余额-预留）校验必须在写锁内二次校验
- P0-3 并发部分退款超退：累计退款不得超过 merchant_income
- P0-3 同 out_refund_no 上游重复退：并发同幂等键只允许上游退一次
- P0-4 过期单回补：查单窗口按 expire_at 口径 + 上游已收款可受控回补

并发手法：两个独立 AsyncSession（同一 sqlite 文件库）+ 在关键读之后注入
`asyncio.sleep` 让出控制权，保证两个调用都读到「未提交的旧状态」后才有人提交，
从而确定性复现 TOCTOU 窗口（不是靠 gather 随机交错）。

**这 7 条是常驻门禁（P0 修复已合入主线，xfail 标记已摘除）**：断言对象是最终
实现——结算打款/驳回的「进程内协作锁 + 结算单行锁 + 状态机复核」、结算申请的
行锁内重算可提余额（`coop_lock` + `lock_merchant`）、退款可退上限的
「订单行锁 + status∈{0,1} 退款单求和（`_reserved_refunded_amount`）」与
`expire_at` 查单窗口，不是修复过程中的中间版本。SQLite 没有 `FOR UPDATE`，
票据里的并发语义在这些用例上由 `coop_lock` 兜住，因此它同样是门禁的一部分：
去掉它（或去掉锁内状态复核）用例就会红（变异验证见下方各用例的注入点说明）。
验收时核对过「修复前的树（`9334aa8`）上 `--runxfail` 全部失败、最终树上全部通过」，
所以它们确实拦得住回归，而不是恒绿的摆设。
"""
import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import (
    Channel,
    Merchant,
    MerchantMessage,
    Order,
    PaymentType,
    Refund,
    Settlement,
)
from app.services.balance import dmoney
from app.services.refund import create_refund
from app.services.settle import apply_settlement, mark_settlement_paid, reject_settlement

# ============================================================
# 并发底座：独立 sqlite 文件库 + WAL + 双会话 + 可控交错
# ============================================================

@pytest_asyncio.fixture
async def conc_engine():
    """并发测试专用库：WAL + 10s busy_timeout，避免 sqlite 写锁把并发压成串行错误。"""
    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(eng.sync_engine, "connect")
    def _on_connect(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()
    try:
        os.remove(db_file)
    except OSError:
        pass


@pytest_asyncio.fixture
async def conc_seed(conc_engine):
    """并发库种子：商户 1001（RSA 公钥）+ 支付宝支付方式 + epay 通道。"""
    from app.services.crypto import encrypt_config
    from tests.conftest import MERCHANT_PUBLIC_KEY, PLATFORM_PRIVATE_KEY, UPSTREAM_KEY
    from app.models import Setting

    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        m = Merchant(
            pid=1001, username="testmch", password="x",
            public_key=MERCHANT_PUBLIC_KEY, status=1,
        )
        s.add(m)
        s.add(Setting(key="platform_rsa_private_key", value=PLATFORM_PRIVATE_KEY))
        pt = PaymentType(code="alipay", name="支付宝", status=1, sort=1)
        s.add(pt)
        await s.commit()
        await s.refresh(m)
        await s.refresh(pt)
        ch = Channel(
            name="测试通道", plugin_name="epay", payment_type_id=pt.id,
            merchant_rate=2, cost_rate=1, status=1, mode=0,
            min_amount=1, max_amount=0, day_limit=0,
            config=encrypt_config({
                "appurl": "https://upstream.example/",
                "appid": "2001",
                "appkey": UPSTREAM_KEY,
                "appswitch": "1",
            }),
        )
        s.add(ch)
        await s.commit()
        await s.refresh(ch)
        return {"merchant_id": m.id, "pay_type_id": pt.id, "channel_id": ch.id}


@pytest_asyncio.fixture
async def sessions(conc_engine):
    """两个独立会话（模拟两个并发请求各自的 DB 会话）。"""
    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    a, b = maker(), maker()
    try:
        yield a, b
    finally:
        await a.close()
        await b.close()


def _delay_reads(session: AsyncSession, delay: float = 0.05) -> None:
    """给 session.get 包一层「读到后让出控制权」的延迟。

    两个并发调用都会先完成状态读取（读到未提交的旧状态）再有人提交，
    这就是 TOCTOU 窗口本身；延迟只让出控制权，不做任何等待，不会死锁。
    """
    orig_get = session.get

    async def _slow_get(model, pk, **kwargs):  # noqa: ANN001
        obj = await orig_get(model, pk, **kwargs)
        await asyncio.sleep(delay)
        return obj

    session.get = _slow_get  # type: ignore[method-assign]


async def _collect(coros):
    """并发执行并收集 (结果, 异常)，异常不影响另一个调用。"""
    return await asyncio.gather(*coros, return_exceptions=True)


async def _set_merchant(session: AsyncSession, merchant_id: int, **fields) -> None:
    m = await session.get(Merchant, merchant_id)
    for k, v in fields.items():
        setattr(m, k, v)
    session.add(m)
    await session.commit()


async def _merchant_row(session: AsyncSession, merchant_id: int) -> Merchant:
    """在**全新会话**上读取商户（避免复用旧快照/触发过期实例的同步懒加载）。"""
    return await session.get(Merchant, merchant_id)


async def _messages_of(session: AsyncSession, merchant_id: int) -> list[MerchantMessage]:
    """在**全新会话**上读取站内消息。"""
    rows = (await session.exec(
        select(MerchantMessage).where(MerchantMessage.merchant_id == merchant_id)
    )).all()
    return list(rows)


async def _two_settlements(session: AsyncSession, merchant_id: int, amount: str = "100.00") -> list[Settlement]:
    """余额里准备两笔冻结（模拟两次已受理的结算申请各 100）。"""
    m = await session.get(Merchant, merchant_id)
    m.balance = dmoney(amount) * 2
    m.frozen_balance = dmoney("0.00")
    m.settle_status = 1
    m.settle_account = "acc"
    m.settle_name = "name"
    session.add(m)
    await session.commit()
    await session.refresh(m)

    created = []
    for _ in range(2):
        settle, err = await apply_settlement(session, m, amount)
        assert err == "", err
        created.append(settle)
        await session.refresh(m)
    return created


# ============================================================
# P0-1 结算双击打款
# ============================================================

async def test_settle_double_pay_processed_once(conc_engine, conc_seed, sessions):
    """同一结算单被并发打款两次：只允许一次成功，冻结额只扣一次。

    用两笔结算（冻结合计 200）作为观测基准：错账会吞掉另一笔的冻结额。
    """
    a, b = sessions
    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        settles = await _two_settlements(s, conc_seed["merchant_id"])
    sid = settles[0].id
    mid = conc_seed["merchant_id"]

    _delay_reads(a)
    _delay_reads(b)
    results = await _collect([
        mark_settlement_paid(a, sid),
        mark_settlement_paid(b, sid),
    ])

    ok = [r for r in results if isinstance(r, tuple) and r[0]]
    assert len(ok) == 1, f"同一结算单被打款多次：{results}"

    async with async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)() as s:
        m = await _merchant_row(s, mid)
        msgs = await _messages_of(s, mid)
        assert dmoney(m.frozen_balance) == dmoney("100.00"), f"冻结额被重复扣减：{m.frozen_balance}"
        assert len([x for x in msgs if "已打款" in (x.title or "")]) == 1


# ============================================================
# P0-1 打款与驳回并发
# ============================================================

async def test_settle_pay_and_reject_are_mutually_exclusive(conc_engine, conc_seed, sessions):
    """打款与驳回并发：只能有一条路径生效，不允许「既打款又退回余额」。"""
    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        settles = await _two_settlements(s, conc_seed["merchant_id"])
    sid = settles[0].id
    mid = conc_seed["merchant_id"]

    a, b = sessions
    _delay_reads(a)
    _delay_reads(b)
    results = await _collect([
        mark_settlement_paid(a, sid),
        reject_settlement(b, sid, "信息有误"),
    ])
    ok = [r for r in results if isinstance(r, tuple) and r[0]]
    assert len(ok) == 1, f"打款与驳回同时生效：{results}"

    async with async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)() as s:
        m = await _merchant_row(s, mid)
        settle = await s.get(Settlement, sid)
        msgs = await _messages_of(s, mid)
        # 两条路径各扣一次冻结额 → 只允许扣一次（另一笔结算的 100 必须还在）
        assert dmoney(m.frozen_balance) == dmoney("100.00"), f"冻结额错账：{m.frozen_balance}"
        # 终态与余额必须自洽：已打款=钱不退回；已驳回=钱退回
        if settle.status == 1:
            assert dmoney(m.balance) == dmoney("0.00"), "已打款却退回了余额"
        else:
            assert dmoney(m.balance) == dmoney("100.00"), "已驳回却未退回余额"
        titles = [x.title or "" for x in msgs]
        assert (len([t for t in titles if "已打款" in t]) + len([t for t in titles if "驳回" in t])) == 1


# ============================================================
# P0-2 并发结算申请（可提余额/预留余额）
# ============================================================

async def test_concurrent_settle_apply_respects_reserved_balance(conc_engine, conc_seed, sessions):
    """两笔并发申请合计不得超过可提余额（余额-预留）：预留 50 必须留在账户里。"""
    mid = conc_seed["merchant_id"]
    setup = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)()
    async with setup as s:
        await _set_merchant(
            s, mid, balance=dmoney("100.00"), frozen_balance=dmoney("0.00"),
            remain_money=dmoney("50.00"), settle_status=1,
            settle_account="acc", settle_name="name",
        )

    a, b = sessions
    _delay_reads(a)
    _delay_reads(b)

    async def _apply(session):
        m = await session.get(Merchant, mid)
        return await apply_settlement(session, m, "50.00")

    results = await _collect([_apply(a), _apply(b)])
    accepted = [r for r in results if isinstance(r, tuple) and r[0] is not None]
    assert len(accepted) <= 1, f"并发申请透支了预留余额：{results}"

    async with async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)() as s:
        m = await _merchant_row(s, mid)
        assert dmoney(m.balance) >= dmoney(m.remain_money) - dmoney("0.00"), (
            f"预留余额被吃掉：balance={m.balance} remain={m.remain_money}"
        )


# ============================================================
# P0-3 并发部分退款超退
# ============================================================

async def _paid_order(session: AsyncSession, mid: int, channel_id: int, trade_no: str,
                      money: str = "100.00", income: str = "98.00") -> Order:
    order = Order(
        trade_no=trade_no, merchant_id=mid, out_trade_no="OT" + trade_no,
        payment_type_id=1, payment_type_code="alipay", subject="p0 test",
        money=dmoney(money), realmoney=dmoney(money), merchant_income=dmoney(income),
        status=1, paid_at=datetime.now(), channel_id=channel_id, plugin_name="epay",
        api_trade_no="UP" + trade_no,
        created_at=datetime.now(), expire_at=datetime.now() + timedelta(minutes=30),
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


def _delay_quota_read(monkeypatch, delay: float = 0.05) -> None:
    """让「锁内配额读」之后让出控制权：并发退款的 TOCTOU 临界读就在这一层。

    最终实现的配额口径是 `_reserved_refunded_amount`（订单行锁内对 status∈{0,1}
    的退款单求和，含在途预占），串行化靠 `coop_lock("refund:<trade_no>")` +
    订单行 `FOR UPDATE`。延迟必须打在这里：注入到 `refunded_amount()`（只算
    成功单）已不在关键段——它测不到「在途预占」口径。
    """
    import app.services.refund as refund_mod

    orig = refund_mod._reserved_refunded_amount

    async def _slow(session, trade_no):
        val = await orig(session, trade_no)
        await asyncio.sleep(delay)
        return val

    monkeypatch.setattr(refund_mod, "_reserved_refunded_amount", _slow)


async def test_concurrent_partial_refund_cannot_over_refund(
    monkeypatch, client, conc_engine, conc_seed, sessions,
):
    """并发部分退款（60 + 50）不得突破可退上限 98，且余额/预占与已退金额自洽。"""
    mid = conc_seed["merchant_id"]
    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await _set_merchant(s, mid, balance=dmoney("98.00"), frozen_balance=dmoney("0.00"))
        order = await _paid_order(s, mid, conc_seed["channel_id"], "2601010000000000000001")
    trade_no = order.trade_no

    _delay_quota_read(monkeypatch)

    a, b = sessions
    m_a = await a.get(Merchant, mid)
    m_b = await b.get(Merchant, mid)
    results = await _collect([
        create_refund(a, m_a, trade_no, "60.00", out_refund_no="P0-A"),
        create_refund(b, m_b, trade_no, "50.00", out_refund_no="P0-B"),
    ])

    async with maker() as s:
        refunds = (await s.exec(select(Refund).where(Refund.trade_no == trade_no))).all()
        total = sum((dmoney(r.amount) for r in refunds), Decimal("0.00"))
        m = await _merchant_row(s, mid)
        assert total <= dmoney("98.00"), f"超退：累计退款 {total} > 可退上限 98（结果 {results}）"
        assert dmoney(m.balance) == dmoney("98.00") - total, (
            f"余额与已退金额不自洽：balance={m.balance} refunded={total}"
        )
        # 60 + 50 装不进 98：只允许一笔成功。
        assert len(refunds) == 1, f"并发退款落库笔数异常：{[ (r.amount, r.status) for r in refunds ]}"
        # 被拒的并发请求不得残留 status=0 预占单——否则它会把后续合法退款的可退额度
        # 永久挤小，也是「零副作用」的对外承诺。
        assert all(r.status == 1 for r in refunds), (
            f"被拒请求残留预占单：{[ (r.amount, r.status) for r in refunds ]}"
        )


# ============================================================
# P0-3 同 out_refund_no 上游重复退
# ============================================================

async def test_same_out_refund_no_hits_upstream_once(
    monkeypatch, client, conc_engine, conc_seed, sessions,
):
    """并发提交同一 out_refund_no：上游只允许被退一次（幂等键必须在 IO 前占位）。"""
    from plugins.epay.plugin import EpayPayment

    mid = conc_seed["merchant_id"]
    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await _set_merchant(s, mid, balance=dmoney("98.00"), frozen_balance=dmoney("0.00"))
        order = await _paid_order(s, mid, conc_seed["channel_id"], "2601010000000000000002")
    trade_no = order.trade_no

    upstream_calls: list[str] = []
    orig_refund = EpayPayment.refund

    async def _counting_refund(self, order_view, refund_no, amount):
        upstream_calls.append(refund_no)
        await asyncio.sleep(0.05)  # 上游会话窗口：两个并发请求都已完成幂等查询
        return await orig_refund(self, order_view, refund_no, amount)

    monkeypatch.setattr(EpayPayment, "refund", _counting_refund)

    a, b = sessions
    m_a = await a.get(Merchant, mid)
    m_b = await b.get(Merchant, mid)
    results = await _collect([
        create_refund(a, m_a, trade_no, "10.00", out_refund_no="SAME-KEY"),
        create_refund(b, m_b, trade_no, "10.00", out_refund_no="SAME-KEY"),
    ])

    assert len(upstream_calls) == 1, f"上游被重复退款 {len(upstream_calls)} 次：{results}"

    async with maker() as s:
        refunds = (await s.exec(select(Refund).where(Refund.trade_no == trade_no))).all()
        m = await _merchant_row(s, mid)
        assert len(refunds) == 1
        assert dmoney(m.balance) == dmoney("88.00"), f"余额扣减次数不对：{m.balance}"


# ============================================================
# P0-4 过期单回补
# ============================================================

async def test_unpaid_scan_window_follows_expire_at(monkeypatch, conc_engine, conc_seed):
    """查单窗口按 expire_at 口径：仍在有效期内的待支付单（哪怕创建已久）必须继续查单。"""
    import app.core.cache as cache_module
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs

    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)

    class _Queue:
        def __init__(self):
            self.calls = []

        async def enqueue(self, name, **kwargs):
            self.calls.append((name, kwargs))

    class _AlwaysLockCache:
        async def get(self, key):
            return None

        async def set(self, key, value, **kwargs):
            return True

        async def delete(self, key):
            return None

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield _AlwaysLockCache()

    monkeypatch.setattr(worker_jobs, "session_maker", maker)
    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)
    fake_queue = _Queue()
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    now = datetime.now()
    mid = conc_seed["merchant_id"]
    async with maker() as s:
        s.add(Order(
            trade_no="2601010000000000000010", merchant_id=mid, out_trade_no="OT10",
            payment_type_id=conc_seed["pay_type_id"], payment_type_code="alipay",
            subject="长时效待支付", money=dmoney("10.00"), status=0,
            channel_id=conc_seed["channel_id"],
            created_at=now - timedelta(minutes=45),      # 创建已久，但仍在有效期内
            expire_at=now + timedelta(minutes=10),
        ))
        await s.commit()

    await worker_jobs.scan_unpaid_orders({"job_id": "cron"})
    trades = [kw.get("trade_no") for _, kw in fake_queue.calls]
    assert "2601010000000000000010" in trades, "有效期内的待支付单被查单窗口漏掉"


async def test_expired_order_paid_upstream_has_backfill_path(monkeypatch, conc_engine, conc_seed):
    """过期单回补：上游确认已收款时必须存在受控回补路径（不得静默丢弃）。

    最终实现口径（P0-4）：过期单（status=3）仍进主动查单；上游回 paid 时
    `process_paid_order` 放行 `late_pay_backfill_hours` 窗口内（默认 24h，0=关闭）
    的补记，窗口外仍会被拒。本用例的过期单 expire_at = now-30min，落在窗口内，
    因此必须入账；管理端手动补单走 `allow_expired=True` 直接跳过窗口判定，
    两条路径共用同一个幂等记账入口。
    """
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs

    from tests.conftest import upstream_state

    maker = async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)
    mid = conc_seed["merchant_id"]
    async with maker() as s:
        order = Order(
            trade_no="2601010000000000000020", merchant_id=mid, out_trade_no="OT20",
            payment_type_id=conc_seed["pay_type_id"], payment_type_code="alipay",
            subject="过期单", money=dmoney("100.00"), realmoney=dmoney("100.00"),
            merchant_income=dmoney("98.00"), status=3,  # 已被 cron 置为已过期
            channel_id=conc_seed["channel_id"], plugin_name="epay",
            api_trade_no="UP2601010000000000000020",
            created_at=datetime.now() - timedelta(hours=1),
            expire_at=datetime.now() - timedelta(minutes=30),
        )
        s.add(order)
        await s.commit()

    monkeypatch.setattr(worker_jobs, "session_maker", maker)
    from app.services import http_client as http_client_module
    import httpx
    from tests.conftest import _mock_upstream_handler
    monkeypatch.setattr(
        http_client_module, "get_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_mock_upstream_handler), timeout=5),
    )
    upstream_state["query_status"] = 1
    try:
        await worker_jobs.query_upstream_job({"job_id": "q"}, "2601010000000000000020")
    finally:
        upstream_state["query_status"] = 0

    async with maker() as s:
        fresh = await s.get(Order, order.id)
        m = await _merchant_row(s, mid)
        assert fresh.status == 1, "上游已收款的过期单被静默丢弃（无回补路径）"
        assert dmoney(m.balance) == dmoney("98.00"), f"回补未入账：{m.balance}"
