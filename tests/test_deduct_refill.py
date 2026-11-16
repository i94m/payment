"""扣量 + 补单（手动回补 / 掉单主动查单补单）整体测试（FREE-33）。

与 test_merchant_p3 的基础扣量用例互补，这里覆盖：
- 频次轮询节律（freq=3 命中第 3/6 笔）、豁免金额不消耗计数
- Redis INCR 路径 vs DB Counter 回退路径
- 充值单不扣量；扣量单重复回调幂等
- 邀请返佣在扣量时扣发、回补时补发
- admin 手动补单：待支付直接记账 + 幂等（重复补单不重复入账）+ 过期单拒绝
- undo_deduct 对非扣量/未支付单拒绝
- 掉单查单补单：未支付不误补 / 金额不符不补 / 已支付不重复 / 补单后通知留痕
"""
import pytest
from sqlmodel import select

from app.models import Merchant, MerchantRecord, NotifyLog, Order
from app.services.balance import dmoney
from app.services.order import undo_deduct
from app.services.settings import set_settings
from tests.conftest import UPSTREAM_KEY, signed_params, upstream_state
from tests.test_pay_flow import _sign_epay  # noqa: F401


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _create_order(client, session, out_trade_no: str, money: str = "100.00") -> Order:
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "扣量补单测试", "money": money,
    }
    params = signed_params(params)
    resp = await client.post("/api/orders", json=params)
    assert resp.json()["code"] == 0, resp.json()
    return (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no))).one()


async def _pay_via_callback(client, order: Order, money: str = "100"):
    """模拟上游回调支付（金额默认与 realmoney=100 匹配）。"""
    notify = {
        "pid": "1001", "name": "扣量补单测试", "money": money,
        "out_trade_no": order.out_trade_no, "trade_no": "UP" + order.out_trade_no,
        "type": "alipay", "trade_status": "TRADE_SUCCESS",
    }
    notify["sign"] = _sign_epay(notify, UPSTREAM_KEY)
    resp = await client.post(f"/callbacks/epay/{order.trade_no}", params=notify)
    assert resp.text == "success", resp.text


def _fresh_maker(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _refetch(engine, trade_no: str) -> Order:
    """全新会话读取最新状态（app 会话的提交对测试会话的旧快照不可见）。"""
    async with _fresh_maker(engine)() as s:
        return (await s.exec(select(Order).where(Order.trade_no == trade_no))).one()


async def _balance(engine, merchant_id: int):
    async with _fresh_maker(engine)() as s:
        m = await s.get(Merchant, merchant_id)
        return m.balance


async def _rows(engine, *entities):
    """全新会话执行查询并返回结果列表（计数/流水断言用）。"""
    from sqlmodel import select as _select
    async with _fresh_maker(engine)() as s:
        return (await s.exec(_select(*entities))).all()




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


# ============================================================
# 扣量节律与豁免
# ============================================================

@pytest.mark.asyncio
async def test_deduct_frequency_3_hits_every_third(client, seeded, session, engine):
    """freq=3：第 3、6 笔命中，其余正常入账；计数跨订单持续。"""
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 3})
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()

    trades = []
    for i in range(1, 8):
        o = await _create_order(client, session, f"FR3-{i}")
        trades.append(o.trade_no)
        await _pay_via_callback(client, o)

    flags = []
    for t in trades:
        o = await _refetch(engine, t)
        flags.append(bool(o.is_deducted))
        assert o.status == 1  # 平台侧全部已支付
    assert flags == [False, False, True, False, False, True, False]

    # 7 笔中 5 笔入账（每笔 98）
    assert await _balance(engine, m.id) == dmoney("490")
    # 扣量单不产生资金流水、不排队通知
    records = await _rows(engine, MerchantRecord)
    assert len(records) == 5
    for t in trades:
        o = await _refetch(engine, t)
        if o.is_deducted:
            assert o.notify_status == 0
        else:
            assert o.notify_status == 1


@pytest.mark.asyncio
async def test_deduct_minmoney_exempt_keeps_counter(client, seeded, session, engine):
    """豁免金额内的单不命中也不消耗计数：30(豁免)→100(计数1)→100(计数2命中)。"""
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 2,
                                 "deduct_minmoney": "50.00"})
    orders = [
        await _create_order(client, session, "EXM-1", "30.00"),
        await _create_order(client, session, "EXM-2", "100.00"),
        await _create_order(client, session, "EXM-3", "100.00"),
    ]
    for o in orders:
        await _pay_via_callback(client, o, money=str(int(float(o.realmoney))))

    flags = [bool((await _refetch(engine, o.trade_no)).is_deducted) for o in orders]
    assert flags == [False, False, True]


@pytest.mark.asyncio
async def test_deduct_recharge_order_not_deducted(client, seeded, session, engine):
    """充值单（order_type=1）不参与扣量，全额入账。"""
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 1})
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()

    o = await _create_order(client, session, "RCH-1", "100.00")
    o.order_type = 1  # 充值单（开放 API 不可造，模拟内部入口）
    session.add(o)
    await session.commit()
    await _pay_via_callback(client, o)

    fresh = await _refetch(engine, o.trade_no)
    assert fresh.is_deducted is False
    assert fresh.status == 1
    assert await _balance(engine, m.id) == dmoney("100")  # 全额入账


@pytest.mark.asyncio
async def test_deducted_duplicate_callback_idempotent(client, seeded, session, engine):
    """扣量单重复回调：应答 success 且不产生任何二次入账/流水。"""
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 1})
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()

    o = await _create_order(client, session, "DUP-1")
    await _pay_via_callback(client, o)
    assert bool((await _refetch(engine, o.trade_no)).is_deducted) is True
    assert await _balance(engine, m.id) == dmoney("0")

    resp = await _pay_via_callback(client, o)  # 重复回调
    assert resp is None or True
    assert await _balance(engine, m.id) == dmoney("0")
    assert await _rows(engine, MerchantRecord) == []
    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 1 and fresh.notify_status == 0


# ============================================================
# 计数路径：Redis INCR vs DB Counter 回退
# ============================================================

class _FakeRedisCache:
    """仅实现 incr 的假缓存：走 _hit_deduct 的 Redis 原子计数分支。"""

    def __init__(self):
        self.n = 0

    async def incr(self, key: str) -> int:
        self.n += 1
        return self.n


@pytest.mark.asyncio
async def test_deduct_counter_redis_and_db_paths(client, seeded, session):
    """Redis 路径 n%freq==0 命中；cache=None 回退走 counters 行（命中后清零）。"""
    from app.services.order import _hit_deduct

    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 2})
    o = await _create_order(client, session, "CNT-1")

    fake = _FakeRedisCache()
    assert await _hit_deduct(session, o, cache=fake) is False   # n=1
    assert await _hit_deduct(session, o, cache=fake) is True    # n=2
    assert await _hit_deduct(session, o, cache=fake) is False   # n=3

    db_cache = None  # 无缓存（测试环境）：走 counters 行锁路径
    assert await _hit_deduct(session, o, cache=db_cache) is False  # counter=1
    assert await _hit_deduct(session, o, cache=db_cache) is True   # counter=2 命中并清零
    assert await _hit_deduct(session, o, cache=db_cache) is False  # counter=1（重新累计）


# ============================================================
# 对外遮蔽口径
# ============================================================

@pytest.mark.asyncio
async def test_deducted_order_query_api_masks(client, seeded, session, engine):
    """开放 API 查单：扣量单对外一律未支付（无完成时间）。"""
    import time

    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 1})
    o = await _create_order(client, session, "MSK-1")
    await _pay_via_callback(client, o)

    params = signed_params({"pid": "1001"})
    resp = await client.get(f"/api/orders/{o.trade_no}", params=params)
    data = resp.json()["data"]
    assert data["status"] == 0
    assert data["endtime"] == ""


# ============================================================
# 邀请返佣：扣量扣发 / 回补补发
# ============================================================

@pytest.mark.asyncio
async def test_invite_rebate_suppressed_then_refilled(client, seeded, session, engine):
    """返佣随扣量扣发，随回补补发（费率 10%，到账 98 → 返 9.80）。"""
    await set_settings(session, {"deduct_enable": True, "deduct_frequency": 2,
                                 "invite_rebate_rate": "10.00"})
    m = await _get_merchant(session)
    inviter = Merchant(pid=2002, username="inviter", password="x",
                       status=1, balance=dmoney("0"))
    session.add(inviter)
    await session.commit()
    m.invited_by = inviter.id
    session.add(m)
    await session.commit()

    o1 = await _create_order(client, session, "REB-1")
    await _pay_via_callback(client, o1)                 # 计数1：正常，返佣 9.80
    o2 = await _create_order(client, session, "REB-2")
    await _pay_via_callback(client, o2)                 # 计数2：命中扣量，不返佣
    assert await _balance(engine, inviter.id) == dmoney("9.80")

    adm = await _admin_headers(session)
    resp = await client.post("/admin-api/order/refill", headers=adm,
                             json={"trade_no": o2.trade_no})
    assert resp.json()["code"] == 0, resp.json()
    assert await _balance(engine, inviter.id) == dmoney("19.60")  # 回补补发返佣


# ============================================================
# admin 手动补单（非扣量路径）
# ============================================================

@pytest.mark.asyncio
async def test_admin_refill_pending_then_idempotent(client, seeded, session, engine):
    """待支付单手动补单：直接记账 + 通知排队；重复补单幂等不重复入账。"""
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()

    o = await _create_order(client, session, "MNL-1")
    adm = await _admin_headers(session)
    r1 = await client.post("/admin-api/order/refill", headers=adm,
                           json={"trade_no": o.trade_no})
    assert r1.json()["code"] == 0, r1.json()
    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 1 and fresh.notify_status == 1
    assert await _balance(engine, m.id) == dmoney("98")

    r2 = await client.post("/admin-api/order/refill", headers=adm,
                           json={"trade_no": o.trade_no})
    assert r2.json()["code"] == 0  # duplicate 也报成功
    assert await _balance(engine, m.id) == dmoney("98")  # 不重复入账
    records = await _rows(engine, MerchantRecord)
    assert [r.order_trade_no for r in records] == [o.trade_no]


@pytest.mark.asyncio
async def test_admin_refill_expired_rejected(client, seeded, session, engine):
    """过期单（status=3）不可补单。"""
    o = await _create_order(client, session, "EXP-1")
    from sqlalchemy import update
    await session.exec(update(Order).where(Order.trade_no == o.trade_no).values(status=3))
    await session.commit()

    r = await client.post("/admin-api/order/refill", headers=await _admin_headers(session),
                          json={"trade_no": o.trade_no})
    assert r.json()["code"] != 0
    # 对管理员只给可操作的中文提示，不把内部状态枚举（invalid 等）甩出来
    assert "不可补单" in r.json()["message"]
    assert "invalid" not in r.json()["message"]


@pytest.mark.asyncio
async def test_undo_deduct_rejects_ineligible(client, seeded, session, engine):
    """undo_deduct 仅接受「已扣量的已支付单」。"""
    o_pending = await _create_order(client, session, "UDR-1")
    assert await undo_deduct(session, o_pending) is False

    o_paid = await _create_order(client, session, "UDR-2")
    await _pay_via_callback(client, o_paid)
    fresh = await _refetch(engine, o_paid.trade_no)
    assert fresh.is_deducted is False
    assert await undo_deduct(session, fresh) is False


# ============================================================
# 掉单主动查单补单（query_upstream_job）
# ============================================================

async def _run_query_job(engine, trade_no: str):
    import app.worker.jobs as jobs_mod

    maker = _fresh_maker(engine)
    real = jobs_mod.session_maker
    jobs_mod.session_maker = maker
    try:
        await jobs_mod.query_upstream_job(None, trade_no)
    finally:
        jobs_mod.session_maker = real


@pytest.mark.asyncio
async def test_query_fill_unpaid_noop(client, seeded, session, engine):
    """上游未支付：不误补。"""
    o = await _create_order(client, session, "QF-1")
    upstream_state["query_status"] = 0
    await _run_query_job(engine, o.trade_no)
    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 0
    assert await _rows(engine, MerchantRecord) == []


@pytest.mark.asyncio
async def test_query_fill_money_mismatch_noop(client, seeded, session, engine):
    """上游金额与订单实付不符（随机金额单实付 5，上游称 100）：拒绝补单。"""
    m = await _get_merchant(session)
    m.random_enable = True
    m.random_min = dmoney("5")
    m.random_max = dmoney("5")
    session.add(m)
    await session.commit()

    o = await _create_order(client, session, "QF-2")           # realmoney=5
    assert o.realmoney == dmoney("5")
    upstream_state["query_status"] = 1                 # 上游称已支付 100
    await _run_query_job(engine, o.trade_no)
    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 0                           # mismatch 拒绝，保持待支付


@pytest.mark.asyncio
async def test_query_fill_already_paid_no_double(client, seeded, session, engine):
    """已支付订单：查单任务直接返回，不重复记账。"""
    o = await _create_order(client, session, "QF-3")
    await _pay_via_callback(client, o)
    upstream_state["query_status"] = 1
    await _run_query_job(engine, o.trade_no)
    records = await _rows(engine, MerchantRecord)
    assert [r.order_trade_no for r in records] == [o.trade_no]


@pytest.mark.asyncio
async def test_query_fill_paid_writes_notify_log(client, seeded, session, engine):
    """补单成功后内联投递商户通知（example.com 失败但有留痕并排重试）。"""
    o = await _create_order(client, session, "QF-4")
    upstream_state["query_status"] = 1
    await _run_query_job(engine, o.trade_no)

    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 1
    records = await _rows(engine, MerchantRecord)
    assert [r.order_trade_no for r in records] == [o.trade_no]
    logs = [l for l in await _rows(engine, NotifyLog) if l.trade_no == o.trade_no]
    assert len(logs) >= 1 and logs[0].kind == "merchant"
    fresh = await _refetch(engine, o.trade_no)
    assert fresh.notify_status == 1          # 失败已排重试
    assert fresh.next_notify_at is not None
