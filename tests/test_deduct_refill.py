"""扣量 + 补单（手动回补 / 掉单主动查单补单）整体测试（FREE-33）。

与 test_merchant_p3 的基础扣量用例互补，这里覆盖：
- 商户级配置（merchants.deduct_*，全局 key 已下架）：频次轮询节律、起扣金额边界、
  商户开关隔离、批量/单商户后台入口；计数按商户独立（A 配 5 与 B 配 8 互不影响）
- 低于起扣金额不消耗计数；恰好等于起扣金额要扣（>= 边界）；门槛比的是买家实付
  realmoney（随机金额单申报额与实付不同），realmoney 为 NULL 时回退申报额 money
- Redis INCR 路径 vs DB Counter 回退路径（两者都按 merchant_id 分行）
- 充值单不扣量；扣量单重复回调幂等
- 邀请返佣在扣量时扣发、回补时补发
- admin 手动补单：待支付直接记账 + 幂等（重复补单不重复入账）+ 过期单受控回补（P0-4）
- undo_deduct 对非扣量/未支付单拒绝
- 掉单查单补单：未支付不误补 / 金额不符不补 / 已支付不重复 / 补单后通知留痕
"""
import pytest
from sqlmodel import select

from app.constants.cache_keys import CacheKeys
from app.models import Counter, Merchant, MerchantRecord, NotifyLog, Order
from app.services.balance import dmoney
from app.services.order import _hit_deduct, undo_deduct
from app.services.settings import set_settings
from tests.conftest import (MERCHANT_PUBLIC_KEY, UPSTREAM_KEY, signed_params,
                            upstream_state)
from tests.test_pay_flow import _sign_epay  # noqa: F401


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _create_order(client, session, out_trade_no: str, money: str = "100.00",
                        pid: str = "1001") -> Order:
    params = {
        "pid": pid, "type": "alipay", "out_trade_no": out_trade_no,
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




async def _enable_deduct(session, frequency: int = 0, minmoney: str = "0.00",
                         enable: bool = True, merchant: Merchant | None = None) -> Merchant:
    """写商户级扣量配置（全局 deduct_* key 已下架，只能改 merchants 行）。"""
    m = merchant or await _get_merchant(session)
    m.deduct_enable = enable
    m.deduct_frequency = frequency
    m.deduct_minmoney = dmoney(minmoney)
    session.add(m)
    await session.commit()
    return m


async def _refetch_merchant(engine, merchant_id: int) -> Merchant:
    async with _fresh_maker(engine)() as s:
        return await s.get(Merchant, merchant_id)


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
    await _enable_deduct(session, 3)
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
async def test_deduct_below_minmoney_keeps_counter(client, seeded, session, engine):
    """低于起扣金额的单不命中也不消耗计数：30(低于50)→100(计数1)→100(计数2命中)。"""
    await _enable_deduct(session, 2, minmoney="50.00")
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
async def test_deduct_minmoney_boundary_equal_is_deducted(client, seeded, session, engine):
    """起扣边界（>=）：money == minmoney 要扣；低于门槛不扣（旧语义是「≤ 豁免」）。"""
    await _enable_deduct(session, 1, minmoney="100.00")
    below = await _create_order(client, session, "BND-1", "50.00")
    equal = await _create_order(client, session, "BND-2", "100.00")
    for o in (below, equal):
        await _pay_via_callback(client, o, money=str(int(float(o.realmoney))))

    assert (await _refetch(engine, below.trade_no)).is_deducted is False
    assert (await _refetch(engine, equal.trade_no)).is_deducted is True


class _DeductOrder:
    """_hit_deduct 只读 merchant_id、money（申报额）、realmoney（实付）三个字段。"""

    def __init__(self, merchant_id: int, money: str = "100.00",
                 realmoney: str | None = None):
        self.merchant_id = merchant_id
        self.money = dmoney(money)
        self.realmoney = dmoney(realmoney) if realmoney is not None else None


async def _enable_random_money(session, merchant: Merchant, low: str, high: str) -> Merchant:
    """开随机金额并把区间固定成 low ~ high（min == max 时 random_money 返回该值）。"""
    merchant.random_enable = True
    merchant.random_min = dmoney(low)
    merchant.random_max = dmoney(high)
    session.add(merchant)
    await session.commit()
    return merchant


@pytest.mark.asyncio
async def test_deduct_minmoney_uses_realmoney_below(client, seeded, session, engine):
    """随机金额商户：申报 1000、实付 3（< 起扣 100）→ 不扣，且不消耗计数。

    旧口径拿申报额 1000 比门槛，会把这笔真实 3 元的交易判成合格单。
    """
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    await _enable_random_money(session, m, "3", "3")
    await _enable_deduct(session, 1, minmoney="100.00", merchant=m)

    o = await _create_order(client, session, "RMN-1", "1000.00")
    assert o.realmoney == dmoney("3")
    await _pay_via_callback(client, o, money="3")

    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 1
    assert fresh.is_deducted is False
    assert await _rows(engine, Counter) == []              # 豁免单不推进刻度
    assert await _balance(engine, m.id) == dmoney("2.94")  # 3 × (1 - 2%) 正常入账


@pytest.mark.asyncio
async def test_deduct_minmoney_realmoney_equal_is_deducted(client, seeded, session, engine):
    """随机金额商户：实付恰好等于起扣金额 → 要扣（>= 边界按实付算）。"""
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    await _enable_random_money(session, m, "100", "100")
    await _enable_deduct(session, 1, minmoney="100.00", merchant=m)

    o = await _create_order(client, session, "RMN-2", "1000.00")
    assert o.realmoney == dmoney("100")
    await _pay_via_callback(client, o, money="100")

    fresh = await _refetch(engine, o.trade_no)
    assert fresh.is_deducted is True
    assert await _balance(engine, m.id) == dmoney("0")


@pytest.mark.asyncio
async def test_deduct_minmoney_falls_back_to_money_when_realmoney_null(
        client, seeded, session, engine):
    """realmoney 为 NULL（未拉起支付）时回退按申报额 money 判定。"""
    m = await _enable_deduct(session, 1, minmoney="100.00")

    # 实付未知、申报 100 → 按 money 比门槛，命中
    assert await _hit_deduct(session, _DeductOrder(m.id, money="100.00"), cache=None) is True
    # 申报 50 < 门槛 → 不扣、不消耗计数
    assert await _hit_deduct(session, _DeductOrder(m.id, money="50.00"), cache=None) is False
    # 有 realmoney 时以其为准：申报 1000 但实付 3 → 不扣
    assert await _hit_deduct(
        session, _DeductOrder(m.id, money="1000.00", realmoney="3.00"), cache=None
    ) is False

    await session.commit()
    counters = await _rows(engine, Counter)
    assert [c.key for c in counters] == [f"deduct_order:{m.id}"]


@pytest.mark.asyncio
async def test_deduct_config_is_per_merchant(client, seeded, session, engine):
    """A 开、B 关：B 的单既不扣也不推进任何计数（计数按商户分行）；开了但频次 0 永不扣。"""
    a = await _get_merchant(session)
    b = Merchant(pid=2103, username="deduct-off", password="x", status=1, balance=dmoney("0"))
    session.add(b)
    await session.commit()
    await session.refresh(b)
    await _enable_deduct(session, 2, merchant=a)

    assert await _hit_deduct(session, _DeductOrder(b.id), cache=None) is False  # B 关：不计数
    assert await _hit_deduct(session, _DeductOrder(a.id), cache=None) is False  # A 计数 1
    assert await _hit_deduct(session, _DeductOrder(b.id), cache=None) is False  # B 仍不计数
    assert await _hit_deduct(session, _DeductOrder(a.id), cache=None) is True   # A 计数 2 命中

    # 计数行按商户分开：只有 A 自己那一行，B 关着不会凭空建行
    await session.commit()
    counters = await _rows(engine, Counter)
    assert [c.key for c in counters] == [f"deduct_order:{a.id}"]

    # 开关开但频次 0：登录/防御口径为「永不扣」（后台已在保存时拦截这种配置）
    await _enable_deduct(session, 0, merchant=a)
    assert await _hit_deduct(session, _DeductOrder(a.id), cache=None) is False


@pytest.mark.asyncio
async def test_deduct_counter_per_merchant_exact(client, seeded, session, engine):
    """验收口径：A 每 5 笔扣 1、B 每 8 笔扣 1，各 40 笔合格单交替下单。

    每商户一根独立刻度 → A 恰好扣 8 笔、B 恰好扣 5 笔（不是「期望接近」）。
    """
    a = await _get_merchant(session)
    b = Merchant(pid=2103, username="deduct-b", password="x", status=1,
                 balance=dmoney("0"), public_key=MERCHANT_PUBLIC_KEY)
    session.add(b)
    await session.commit()
    await session.refresh(b)
    await _enable_deduct(session, 5, merchant=a)
    await _enable_deduct(session, 8, merchant=b)

    trades = {"a": [], "b": []}
    for i in range(1, 41):
        for tag, pid in (("a", "1001"), ("b", "2103")):
            o = await _create_order(client, session, f"PM-{tag.upper()}-{i}", pid=pid)
            trades[tag].append(o.trade_no)
            await _pay_via_callback(client, o)

    # 逐单读回最新状态，统计命中数
    flags = {}
    for tag in ("a", "b"):
        flags[tag] = []
        for t in trades[tag]:
            flags[tag].append(bool((await _refetch(engine, t)).is_deducted))

    assert flags["a"].count(True) == 8   # 40 // 5
    assert flags["b"].count(True) == 5   # 40 // 8
    # 每根刻度各自循环：第 N、2N … 笔命中，间距固定
    assert flags["a"] == [i % 5 == 4 for i in range(40)]
    assert flags["b"] == [i % 8 == 7 for i in range(40)]


@pytest.mark.asyncio
async def test_deduct_admin_endpoints(client, seeded, session, engine):
    """后台入口：批量开必须带频次；批量关只写开关位；单商户可设频次与起扣金额。"""
    adm = await _admin_headers(session)
    m = await _get_merchant(session)

    # 批量开不了：没有频次 → 拒绝（否则开完全部 frequency=0，永不扣）
    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": True})
    assert r.json()["code"] != 0

    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": True, "deduct_frequency": 5})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert fresh.deduct_enable is True and fresh.deduct_frequency == 5

    # 单商户：频次 + 起扣金额（金额按 Decimal 落库两位小数）
    r = await client.post(f"/admin-api/merchant/{m.id}/deduct", headers=adm,
                          json={"deduct_enable": True, "deduct_frequency": 7,
                                "deduct_minmoney": "100"})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert fresh.deduct_frequency == 7
    assert str(fresh.deduct_minmoney) == "100.00"
    assert fresh.deduct_enable is True

    # 紧急停止：批量关只写开关位，频次与起扣金额原样保留（恢复不用凭记忆重配）
    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": False})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert fresh.deduct_enable is False
    assert fresh.deduct_frequency == 7
    assert str(fresh.deduct_minmoney) == "100.00"


@pytest.mark.asyncio
async def test_deduct_batch_enable_writes_frequency_and_minmoney(client, seeded, session, engine):
    """批量开：频次与起扣金额一起下发到全部商户；金额留空按 0（不限金额）。"""
    adm = await _admin_headers(session)
    m = await _get_merchant(session)
    other = Merchant(pid=2104, username="deduct-batch", password="x", status=1,
                     balance=dmoney("0"), deduct_minmoney=dmoney("88.00"))
    session.add(other)
    await session.commit()
    await session.refresh(other)

    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": True, "deduct_frequency": 6,
                                "deduct_minmoney": "50"})
    assert r.json()["code"] == 0, r.json()
    for mid in (m.id, other.id):
        fresh = await _refetch_merchant(engine, mid)
        assert fresh.deduct_enable is True
        assert fresh.deduct_frequency == 6
        assert str(fresh.deduct_minmoney) == "50.00"  # 覆盖原 88.00

    # 金额留空 = 0（不限金额），与单商户表单一致
    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": True, "deduct_frequency": 6})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert str(fresh.deduct_minmoney) == "0.00"

    # 负数金额拒收
    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": True, "deduct_frequency": 6,
                                "deduct_minmoney": "-1"})
    assert r.json()["code"] != 0


@pytest.mark.asyncio
async def test_deduct_batch_disable_keeps_frequency_and_minmoney(client, seeded, session, engine):
    """批量关：只写开关位，频次与起扣金额原样保留（即便请求里带了值也不动）。"""
    adm = await _admin_headers(session)
    m = await _get_merchant(session)
    await _enable_deduct(session, 9, minmoney="123.45", merchant=m)

    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": False})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert fresh.deduct_enable is False
    assert fresh.deduct_frequency == 9
    assert str(fresh.deduct_minmoney) == "123.45"

    # 关闭时即使带了频次/金额也不写入（只认开关位，避免误抹配置）
    await _enable_deduct(session, 9, minmoney="123.45", merchant=m)
    r = await client.post("/admin-api/merchant/deduct/set-all", headers=adm,
                          json={"deduct_enable": False, "deduct_frequency": 1,
                                "deduct_minmoney": "0"})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert fresh.deduct_enable is False
    assert fresh.deduct_frequency == 9
    assert str(fresh.deduct_minmoney) == "123.45"


@pytest.mark.asyncio
async def test_deduct_single_update_keeps_unpassed_fields(client, seeded, session, engine):
    """单商户接口：只改开关（不带频次/金额）不得清掉已有配置；显式传 0 才是「不限金额」。"""
    adm = await _admin_headers(session)
    m = await _get_merchant(session)

    r = await client.post(f"/admin-api/merchant/{m.id}/deduct", headers=adm,
                          json={"deduct_enable": True, "deduct_frequency": 7,
                                "deduct_minmoney": "100"})
    assert r.json()["code"] == 0, r.json()

    # 只改开关：未传 deduct_minmoney / deduct_frequency → 两者都保留
    r = await client.post(f"/admin-api/merchant/{m.id}/deduct", headers=adm,
                          json={"deduct_enable": True})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert str(fresh.deduct_minmoney) == "100.00"
    assert fresh.deduct_frequency == 7
    assert fresh.deduct_enable is True

    # 显式传 0 = 不限金额（与「未传」区分开）
    r = await client.post(f"/admin-api/merchant/{m.id}/deduct", headers=adm,
                          json={"deduct_enable": True, "deduct_minmoney": "0"})
    assert r.json()["code"] == 0, r.json()
    fresh = await _refetch_merchant(engine, m.id)
    assert str(fresh.deduct_minmoney) == "0.00"
    assert fresh.deduct_frequency == 7  # 频次同样不受影响


@pytest.mark.asyncio
async def test_deduct_recharge_order_not_deducted(client, seeded, session, engine):
    """充值单（order_type=1）不参与扣量，全额入账。"""
    await _enable_deduct(session, 1)
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
    await _enable_deduct(session, 1)
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
    """仅实现 incr 的假缓存：走 _hit_deduct 的 Redis 原子计数分支，并记录每根 key 的值。"""

    def __init__(self):
        self.counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


@pytest.mark.asyncio
async def test_deduct_counter_redis_and_db_paths(client, seeded, session, engine):
    """Redis 路径 n%freq==0 命中，key 带 merchant_id；cache=None 回退 counters 行
    （key 同样带 merchant_id，命中后清零重新累计）。"""
    m = await _enable_deduct(session, 2)
    o = await _create_order(client, session, "CNT-1")

    fake = _FakeRedisCache()
    assert await _hit_deduct(session, o, cache=fake) is False   # n=1
    assert await _hit_deduct(session, o, cache=fake) is True    # n=2
    assert await _hit_deduct(session, o, cache=fake) is False   # n=3
    # Redis key 按商户隔离，不再有全平台共用刻度
    assert fake.counts == {CacheKeys.DEDUCT_ORDER_COUNTER.format(merchant_id=m.id): 3}

    db_cache = None  # 无缓存（测试环境）：走 counters 行锁路径
    assert await _hit_deduct(session, o, cache=db_cache) is False  # counter=1
    assert await _hit_deduct(session, o, cache=db_cache) is True   # counter=2 命中并清零
    assert await _hit_deduct(session, o, cache=db_cache) is False  # counter=1（重新累计）
    await session.commit()
    rows = await _rows(engine, Counter)
    assert [c.key for c in rows] == [f"deduct_order:{m.id}"]


# ============================================================
# 对外遮蔽口径
# ============================================================

@pytest.mark.asyncio
async def test_deducted_order_query_api_masks(client, seeded, session, engine):
    """开放 API 查单：扣量单对外一律未支付（无完成时间）。"""
    import time

    await _enable_deduct(session, 1)
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
    await _enable_deduct(session, 2)
    await set_settings(session, {"invite_rebate_rate": "10.00"})
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
async def test_admin_refill_expired_controlled_backfill(client, seeded, session, engine):
    """过期单（status=3）走受控回补：管理员确认已收款后可补单入账（P0-4）。

    原行为是「过期一律拒单」，导致上游已收款但平台无法入账、只能改库。
    现在后台手动补单是显式的人为确认，不受 late_pay_backfill_hours 窗口限制。
    """
    m = await _get_merchant(session)
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()

    o = await _create_order(client, session, "EXP-1")
    from sqlalchemy import update
    await session.exec(update(Order).where(Order.trade_no == o.trade_no).values(status=3))
    await session.commit()

    r = await client.post("/admin-api/order/refill", headers=await _admin_headers(session),
                          json={"trade_no": o.trade_no})
    assert r.json()["code"] == 0, r.json()
    assert "回补" in r.json()["message"]
    # 不把内部状态枚举（invalid 等）甩给管理员
    assert "invalid" not in r.json()["message"]

    fresh = await _refetch(engine, o.trade_no)
    assert fresh.status == 1 and fresh.notify_status == 1
    assert await _balance(engine, m.id) == dmoney("98")

    # 幂等：再补一次不入账
    r2 = await client.post("/admin-api/order/refill", headers=await _admin_headers(session),
                           json={"trade_no": o.trade_no})
    assert r2.json()["code"] == 0
    assert await _balance(engine, m.id) == dmoney("98")


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
