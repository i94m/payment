"""通道限速 Redis 原子占坑测试（Channel::reserveChannelSlot 语义）。

窗口数学、Lua 占坑桩、读侧预过滤、choose_channel 换通道/全满行为。
"""
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import DisabledCache
from app.services.balance import dmoney
from app.services.channel import choose_channel
from app.services.home_config import set_ratelimit_one
from app.services.ratelimit import (
    LIMIT_WINDOW,
    RESERVE_LUA,
    exceeded_channel_ids_redis,
    per_window_limit,
    reserve_channel_slot,
    reserve_key,
    window_start,
)
from tests.test_home_dashboard import two_channels  # noqa: F401  复用通道夹具

# 固定"当前时间"，保证窗口键可预测（6_000_000_000 整除窗口，_NOW 为窗口内第 2 秒）
_NOW_WINDOW = 6_000_000_000
_NOW = _NOW_WINDOW + 2


class FakeRedis:
    """最小 Redis 桩：按 RESERVE_LUA 的 INCR+首 INCR 设 TTL 语义维护键值计数。"""

    def __init__(self):
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def eval(self, script, numkeys, key, ttl):
        assert script == RESERVE_LUA
        self.store[key] = self.store.get(key, 0) + 1
        if self.store[key] == 1:
            self.ttls[key] = ttl
        return self.store[key]

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]


# ============================================================
# 窗口数学
# ============================================================

def test_window_start_aligns_to_epoch():
    assert window_start(_NOW) == _NOW_WINDOW
    assert window_start(_NOW) % LIMIT_WINDOW == 0
    assert window_start(_NOW_WINDOW + LIMIT_WINDOW - 1) == _NOW_WINDOW          # 窗口末秒仍在同窗
    assert window_start(_NOW_WINDOW + LIMIT_WINDOW) == _NOW_WINDOW + LIMIT_WINDOW  # 下一窗即新起点


def test_per_window_limit_ceil_and_min_one():
    assert per_window_limit(60) == 6                     # ceil(60*6/60)=6
    assert per_window_limit(10) == 1                     # 10/分钟 → 每 6 秒 1 个
    assert per_window_limit(9) == 1                      # 不足 10/分钟被放大到 10/分钟
    assert per_window_limit(0) == 1                      # 至少 1
    assert per_window_limit(17) == 2                     # ceil(1.7)=2


def test_reserve_key_contains_window_start():
    assert reserve_key(86, _NOW) == f"chan:rl:86:{_NOW_WINDOW}"


# ============================================================
# 原子占坑 / 读侧预过滤
# ============================================================

async def test_reserve_slot_true_then_false_when_full():
    cache = FakeRedis()
    # 10/分钟 → 单窗口 1 个名额：同窗口第一个占坑成功、第二个被拒
    first = await reserve_channel_slot(cache, 86, 10)
    second = await reserve_channel_slot(cache, 86, 10)
    assert {first, second} == {True, False}
    # 首 INCR 才设 TTL = 窗口 + 5 秒余量
    assert list(cache.ttls.values()) == [LIMIT_WINDOW + 5]


async def test_reserve_slot_fail_open_signals():
    # None / DisabledCache / 异常 → None（调用方回退或放行）
    assert await reserve_channel_slot(None, 86, 10) is None
    assert await reserve_channel_slot(DisabledCache(), 86, 10) is None
    assert await reserve_channel_slot(FakeRedis(), 86, 0) is None  # 未配置限速


async def test_exceeded_redis_read_side():
    cache = FakeRedis()
    ratelimit_map = {86: 10, 87: 600}
    # 86 号通道窗口计数已满（10/分钟 → 1 个/窗口），87 未占坑
    cache.store[reserve_key(86, _NOW)] = per_window_limit(10)
    exceeded = await exceeded_channel_ids_redis(cache, ratelimit_map, now=_NOW)
    assert exceeded == {86}

    # 空 map / Redis 不可用
    assert await exceeded_channel_ids_redis(cache, {}, now=_NOW) == set()
    assert await exceeded_channel_ids_redis(None, ratelimit_map, now=_NOW) is None
    assert await exceeded_channel_ids_redis(DisabledCache(), ratelimit_map, now=_NOW) is None


# ============================================================
# choose_channel：占坑满换通道 / 全满返回 None
# ============================================================

@pytest.mark.asyncio
async def test_choose_channel_rotates_on_full_window(session: AsyncSession, two_channels):
    """两通道各限 1 次/窗口：第一单占 A、第二单占 B、第三单无通道可用。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    await set_ratelimit_one(session, seeded["channel"].id, 10)   # 10/分钟 → 每窗口 1 个
    await set_ratelimit_one(session, seeded["channel2"].id, 10)

    cache = FakeRedis()
    first = await choose_channel(session, cache, tid, dmoney("10.00"))
    second = await choose_channel(session, cache, tid, dmoney("10.00"))
    assert first is not None and second is not None
    assert first.channel.id != second.channel.id
    # 第三个候选全满
    third = await choose_channel(session, cache, tid, dmoney("10.00"))
    assert third is None


@pytest.mark.asyncio
async def test_choose_channel_unlimited_when_no_ratelimit(session: AsyncSession, two_channels):
    """未配置限速：不占坑、行为与原逻辑一致。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    cache = FakeRedis()
    resolved = await choose_channel(session, cache, tid, dmoney("10.00"))
    assert resolved is not None
    assert cache.store == {}  # 没有限速配置 → 无占坑写入


@pytest.mark.asyncio
async def test_choose_channel_db_fallback_when_redis_down(session: AsyncSession, two_channels):
    """Redis 不可用（cache=None）：回退 DB 双窗口检查，超限通道仍被剔除。"""
    from tests.test_home_dashboard import _mk_order

    seeded = two_channels
    tid = seeded["pay_type"].id
    ch1 = seeded["channel"]
    await set_ratelimit_one(session, ch1.id, 1)  # ch1 每分钟 1 单
    session.add(_mk_order(1, ch1.id, tid, created_min=0))
    await session.commit()

    resolved = await choose_channel(session, None, tid, dmoney("10.00"))
    # ch1 限速中 → 只会选到 ch2
    assert resolved is not None
    assert resolved.channel.id == seeded["channel2"].id
