"""通道限速 Redis 原子占坑。

窗口为 6 秒固定桶（epoch 对齐，非滑动），单窗口名额 = ceil(限速/60*窗口秒)（至少 1）。
读侧 MGET 批量预过滤已达上限的通道；写侧建单前 Lua 占坑（INCR+EXPIRE 一次 RTT 完成，
Redis 脚本引擎保证原子，并发不会超发）。计数键带窗口起点，窗口+余量秒后自动过期，无需清理。

Redis 不可用时读侧/写侧均返回 None，调用方回退 DB 双窗口检查（exceeded_channel_ids），
不阻塞支付（可用性优先，与 _check_day_limit 的 fail-open 语义一致）。
"""
import math
import time

from app.core.config import config

LIMIT_WINDOW = 6  # 窗口秒数

# INCR + 首次 EXPIRE 用 Lua 一次往返完成（仅第一个 INCR 置 TTL，避免重置过期时间）
RESERVE_LUA = (
    "local c = redis.call('INCR', KEYS[1]) "
    "if c == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1])) end "
    "return c"
)


def window_start(now: int | float | None = None) -> int:
    """当前固定窗口起点（epoch 秒向下取整到窗口边界）。"""
    ts = int(now if now is not None else time.time())
    return ts // LIMIT_WINDOW * LIMIT_WINDOW


def reserve_key(channel_id: int, now: int | float | None = None) -> str:
    """限速计数键：{prefix}chan:rl:{通道ID}:{窗口起点}（沿用 chan: 前缀风格，可被 cache clear 清理）。"""
    prefix = config.CACHE_PREFIX or ""
    return f"{prefix}chan:rl:{int(channel_id)}:{window_start(now)}"


def per_window_limit(limit_per_minute: int) -> int:
    """分钟速率换算为单窗口名额（至少 1；N<10/分钟 会被放大到 10/分钟）。"""
    return max(1, math.ceil(limit_per_minute * LIMIT_WINDOW / 60))


async def exceeded_channel_ids_redis(
    cache, ratelimit_map: dict[int, int], now: int | float | None = None
) -> set[int] | None:
    """读侧预过滤：MGET 批量读各通道当前窗口计数，返回已达上限的通道集合。

    Redis 不可用返回 None，调用方回退 exceeded_channel_ids 的 DB 双窗口检查。
    """
    if not ratelimit_map:
        return set()
    if cache is None:
        return None
    try:
        ws = window_start(now)
        keys = [f"{config.CACHE_PREFIX or ''}chan:rl:{cid}:{ws}" for cid in ratelimit_map]
        values = await cache.mget(keys)
    except Exception:
        return None
    exceeded: set[int] = set()
    for cid, value in zip(ratelimit_map, values):
        limit = ratelimit_map[cid]
        if limit > 0 and value is not None and int(value) >= per_window_limit(limit):
            exceeded.add(cid)
    return exceeded


async def reserve_channel_slot(cache, channel_id: int, limit_per_minute: int) -> bool | None:
    """建单前原子占坑。

    返回 True=有名额；False=本窗口已满；None=Redis 不可用/未启用限速
    （调用方结合读侧结果决定放行或回退，不阻塞支付）。
    """
    if cache is None or limit_per_minute <= 0:
        return None
    try:
        count = await cache.eval(RESERVE_LUA, 1, reserve_key(channel_id), LIMIT_WINDOW + 5)
    except Exception:
        return None
    if count is None:
        return None
    return int(count) <= per_window_limit(limit_per_minute)
