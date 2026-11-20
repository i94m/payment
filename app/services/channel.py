"""通道路由（3 种模式）。

选择流程：按支付方式取启用通道 → 金额区间过滤 → 当日限笔/限额过滤（Redis 计数与金额累计）
→ 按通道 mode 排定候选：0=随机 / 1=顺序（usetime 游标）/ 2=权重随机 → 逐个 Redis 原子占坑
（限速窗口内拿不到名额则换下一个候选）。
"""
import random
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import CacheClient
from app.core.config import config
from app.models import Channel, ChannelCode, ChannelCodeItem, PaymentType
from app.services.balance import dmoney
from app.services.crypto import decrypt_config
from app.services.home_config import (
    exceeded_channel_ids,
    get_payroute_config,
    get_ratelimit_map,
    money_ok,
)
from app.services.ratelimit import exceeded_channel_ids_redis, reserve_channel_slot


@dataclass
class ResolvedChannel:
    channel: Channel
    config: dict                 # 解密后的凭证
    merchant_rate: Decimal       # 实际商户费率%（商户覆盖 > 通道）
    cost_rate: Decimal           # 成本费率%


def _day_key(channel_id: int) -> str:
    """当日成交笔数键（单日限笔）。"""
    prefix = config.CACHE_PREFIX or ""
    return f"{prefix}chan:day:{channel_id}:{datetime.now().strftime('%Y%m%d')}"


def _day_amount_key(channel_id: int) -> str:
    """当日累计金额键（单日限额）。"""
    prefix = config.CACHE_PREFIX or ""
    return f"{prefix}chan:dayamt:{channel_id}:{datetime.now().strftime('%Y%m%d')}"


async def _check_day_limit(cache: CacheClient | None, channel: Channel, money: Decimal) -> bool:
    """当日限笔/限额检查。超限返回 False，0 表示不限。

    - day_limit_count：当日成交笔数上限（Redis INCR 计数）
    - day_limit：当日累计金额上限（Redis INCRBYFLOAT 按单笔金额累计）
    超限时回滚本次计数/金额，不占用当日额度。
    """
    if cache is None or (channel.day_limit <= 0 and channel.day_limit_count <= 0):
        return True
    try:
        ok = True
        if channel.day_limit_count > 0:
            key = _day_key(channel.id)
            count = await cache.incr(key)
            if count == 1:
                await cache.expire(key, 90000)  # 25 小时兜底过期
            if count > channel.day_limit_count:
                await cache.decr(key)  # 回滚计数，不占用额度
                ok = False
        if ok and channel.day_limit > 0:
            key = _day_amount_key(channel.id)
            total = await cache.incrbyfloat(key, str(money))
            if await cache.ttl(key) == -1:
                await cache.expire(key, 90000)
            if Decimal(str(total)) > channel.day_limit:
                await cache.incrbyfloat(key, str(-money))  # 回滚金额
                ok = False
        return ok
    except Exception:
        # Redis 不可用时放行（可用性优先），不计限额
        return True


def _weighted_random(channels: list[Channel]) -> Channel:
    total = sum(max(c.weight, 0) for c in channels) or len(channels)
    point = random.uniform(0, total)
    acc = 0
    for c in channels:
        acc += max(c.weight, 0)
        if point <= acc:
            return c
    return channels[-1]


def _ordered_candidates(available: list[Channel], mode: int) -> list[Channel]:
    """按通道 mode 排定占坑尝试顺序：0=随机 / 1=顺序（usetime 最旧优先）/ 2=权重随机（不放回抽取）。"""
    if mode == 1:
        return sorted(available, key=lambda c: (c.usetime, c.id))
    if mode == 2 and len(available) > 1:
        pool, ordered = list(available), []
        while pool:
            pick = _weighted_random(pool)
            ordered.append(pick)
            pool.remove(pick)
        return ordered
    random.shuffle(available)
    return available


async def _exceeded_ids(
    session: AsyncSession, cache: CacheClient | None, ratelimit_map: dict[int, int]
) -> set[int]:
    """限速读侧预过滤：Redis 不可用时回退 DB 双窗口检查。"""
    if not ratelimit_map:
        return set()
    exceeded = await exceeded_channel_ids_redis(cache, ratelimit_map)
    if exceeded is None:
        exceeded = await exceeded_channel_ids(session, ratelimit_map)
    return exceeded


async def _available_channels(
    session: AsyncSession,
    cache: CacheClient | None,
    rows: list[Channel],
    money: Decimal,
    exceeded: set[int],
    skip_limits: bool,
) -> list[Channel]:
    """金额区间过滤 → 剔除限速中通道 → 当日限额过滤（两种路由共用的候选流水线）。"""
    candidates = [
        c for c in rows
        if money >= c.min_amount and (c.max_amount <= 0 or money <= c.max_amount)
    ]
    available: list[Channel] = []
    for c in candidates:
        if c.id in exceeded:
            continue
        if skip_limits or await _check_day_limit(cache, c, money):
            available.append(c)
    return available


async def choose_channel(
    session: AsyncSession,
    cache: CacheClient | None,
    payment_type_id: int,
    money: Decimal,
    merchant_rate: Decimal | None = None,
    skip_limits: bool = False,
) -> ResolvedChannel | None:
    """为订单选择通道。无可用通道返回 None。

    通道限速（ratelimit_<cid>）
    读侧 MGET 预过滤（Redis 不可用回退 DB 双窗口检查）→ 金额路由（金额落在配置区间时
    优先走固定支付通道，占坑失败回退正常选择）→ 候选按 mode 排序后逐个 Redis 原子占坑，
    窗口已满换下一个候选。

    skip_limits=True 时跳过限速占坑与日限额累计（测试单专用）：测试不该消耗真实额度，
    否则反复测试可能把通道测到 daystatus 超限自动关闭。
    """
    money = dmoney(money)
    rows = (await session.exec(
        select(Channel)
        .where(Channel.payment_type_id == payment_type_id, Channel.status == 1)
        .order_by(Channel.sort, Channel.id)
    )).all()
    if not rows:
        return None

    # 通道限速：读侧预过滤已达速率上限的通道集合
    ratelimit_map = {} if skip_limits else await get_ratelimit_map(session)
    exceeded = await _exceeded_ids(session, cache, ratelimit_map)

    # 金额路由：金额落在 [min, max] 时优先走固定通道（须启用且未限速）
    route = (await get_payroute_config(session)).get(payment_type_id)
    if route and route.get("enable") == 1 and money_ok(route.get("min", "0"), route.get("max", "0"), money):
        target_id = int(route.get("channel") or 0)
        target = next((c for c in rows if c.id == target_id and c.id not in exceeded), None)
        if target is not None and (
            skip_limits
            or await reserve_channel_slot(cache, target.id, ratelimit_map.get(target.id, 0)) is not False
        ):
            return _resolve(session, target, merchant_rate)
        # 固定通道窗口已满 → 回退正常选择

    available = await _available_channels(session, cache, rows, money, exceeded, skip_limits)
    if not available:
        return None

    # 按 mode 排定候选后逐个占坑：未限速/Redis 不可用直接通过；窗口已满换下一个
    mode = available[0].mode
    for c in _ordered_candidates(available, mode):
        if skip_limits or await reserve_channel_slot(cache, c.id, ratelimit_map.get(c.id, 0)) is not False:
            return _resolve(session, c, merchant_rate)
    return None


# ============================================================
# 下游通道编码（channel_code）：限定候选集的路由
# ============================================================

def normalize_channel_code(raw) -> str:
    """编码归一：去空白 + 转小写（下游大小写不敏感，落库/比对统一小写）。"""
    return str(raw or "").strip().lower()


def code_format_ok(code: str) -> bool:
    """编码格式：1~32 位小写字母/数字/`-`/`_`（对外契约，不含中文与空格）。"""
    if not code or len(code) > 32:
        return False
    return all(ch.isalnum() or ch in "-_" for ch in code) and code.isascii()


async def get_channel_code(session: AsyncSession, code: str) -> ChannelCode | None:
    """按编码字符串取主档（调用方先用 normalize_channel_code 归一）。"""
    if not code:
        return None
    return (await session.exec(select(ChannelCode).where(ChannelCode.code == code))).first()


async def code_supports_type(session: AsyncSession, code_id: int, payment_type_id: int) -> bool:
    """编码下是否有与该支付方式绑定的通道（只看绑定关系，不看临时开关）。

    建单时的参数级校验用它：配置错误（编码不支持这个支付方式）要立刻告诉下游，
    而不是先给一张永远拉不起来的单。
    """
    if not code_id:
        return False
    row = (await session.exec(
        select(ChannelCodeItem.id)
        .join(Channel, col(Channel.id) == ChannelCodeItem.channel_id)
        .where(
            ChannelCodeItem.code_id == code_id,
            Channel.payment_type_id == payment_type_id,
        )
        .limit(1)
    )).first()
    return row is not None


def _weighted_order(pairs: list[tuple[Any, int]]) -> list[Any]:
    """按权重不放回抽签。

    权重全为 0 时**退化为随机**（保存时已拦截，这里只是防御）：老实现会退化成
    「永远选最后一条」，运营配错时全量单压到同一条通道，比随机更危险。
    """
    pool = list(pairs)
    ordered: list[Any] = []
    if sum(max(w, 0) for _, w in pool) <= 0:
        picks = [obj for obj, _ in pool]
        random.shuffle(picks)
        return picks
    while pool:
        total = sum(max(w, 0) for _, w in pool)
        point = random.uniform(0, total)
        acc = 0
        chosen_idx = len(pool) - 1
        for idx, (_, w) in enumerate(pool):
            acc += max(w, 0)
            if point <= acc:
                chosen_idx = idx
                break
        ordered.append(pool.pop(chosen_idx)[0])
    return ordered


def _order_code_candidates(
    available: list[Channel], mode: int, item_map: dict[int, ChannelCodeItem]
) -> list[Channel]:
    """按**编码级** mode 排定候选：0=随机 / 1=轮询（绑定行游标）/ 2=权重。"""
    if mode == 1:
        return sorted(
            available,
            key=lambda c: (item_map[c.id].last_used_at, item_map[c.id].sort, c.id),
        )
    if mode == 2 and len(available) > 1:
        return _weighted_order([(c, item_map[c.id].weight) for c in available])
    pool = list(available)
    random.shuffle(pool)
    return pool


def _resolve_by_code(
    session: AsyncSession,
    chosen: Channel,
    item: ChannelCodeItem,
    siblings: list[ChannelCodeItem],
    merchant_rate: Decimal | None,
) -> ResolvedChannel:
    """编码选定通道后推进**绑定行**的轮询游标（不碰 channels.usetime）。

    同一通道被多个编码引用时，各编码的轮询刻度互不干扰；默认路由的顺序游标也不受影响。
    游标取 `max(now_ms, 本编码最大游标 + 1)`：同一毫秒内连续两单也能严格递增，
    否则同毫秒的两单会因并列而反复命中同一条通道（轮询退化成固定）。
    """
    now_ms = int(time.time() * 1000)
    latest = max((it.last_used_at for it in siblings), default=0)
    item.last_used_at = max(now_ms, latest + 1) if latest >= now_ms else now_ms
    session.add(item)
    rate = merchant_rate if merchant_rate is not None else chosen.merchant_rate
    return ResolvedChannel(
        channel=chosen,
        config=decrypt_config(chosen.config),
        merchant_rate=dmoney(rate),
        cost_rate=dmoney(chosen.cost_rate),
    )


async def choose_channel_by_code(
    session: AsyncSession,
    cache: CacheClient | None,
    code_id: int,
    payment_type_id: int,
    money: Decimal,
    merchant_rate: Decimal | None = None,
    skip_limits: bool = False,
) -> ResolvedChannel | None:
    """在通道编码限定范围内选通道。编码停用 / 无绑定 / 编码内全不可用 → None。

    与 choose_channel 的差别只有两处：候选集来自 `channel_code_items`（且只看本编码内
    启用的成员 + 成员通道全局启用 + 该支付方式），策略取自编码主档的 mode。
    **金额路由（payroute_config）不参与**：编码是运营对下游承诺的边界，叠加平台侧兜底
    分流会把单甩到编码之外。

    绝不回落到编码之外的通道：这里返回 None 由调用方报「编码当前不可用」，不转投默认路由。
    """
    money = dmoney(money)
    code = await session.get(ChannelCode, code_id)
    if not code or code.status != 1:
        return None
    items = (await session.exec(
        select(ChannelCodeItem).where(
            col(ChannelCodeItem.code_id) == code_id,
            col(ChannelCodeItem.status) == 1,
        )
    )).all()
    if not items:
        return None
    item_map = {it.channel_id: it for it in items}
    rows = (await session.exec(
        select(Channel)
        .where(
            col(Channel.id).in_(list(item_map)),
            Channel.payment_type_id == payment_type_id,
            Channel.status == 1,
        )
        .order_by(Channel.sort, Channel.id)
    )).all()
    if not rows:
        return None

    ratelimit_map = {} if skip_limits else await get_ratelimit_map(session)
    exceeded = await _exceeded_ids(session, cache, ratelimit_map)
    available = await _available_channels(session, cache, rows, money, exceeded, skip_limits)
    if not available:
        return None

    for c in _order_code_candidates(available, code.mode, item_map):
        if skip_limits or await reserve_channel_slot(cache, c.id, ratelimit_map.get(c.id, 0)) is not False:
            return _resolve_by_code(session, c, item_map[c.id], items, merchant_rate)
    return None



def _resolve(session: AsyncSession, chosen: Channel, merchant_rate: Decimal | None) -> ResolvedChannel:
    """通道选定后更新轮询游标并组装结果。"""
    chosen.usetime = int(time.time() * 1000)
    session.add(chosen)
    rate = merchant_rate if merchant_rate is not None else chosen.merchant_rate
    return ResolvedChannel(
        channel=chosen,
        config=decrypt_config(chosen.config),
        merchant_rate=dmoney(rate),
        cost_rate=dmoney(chosen.cost_rate),
    )


async def get_channel_with_config(
    session: AsyncSession, channel_id: int
) -> tuple[Channel | None, dict]:
    """按 ID 加载通道 + 解密凭证（回调网关/查单用）。"""
    channel = await session.get(Channel, channel_id)
    if not channel:
        return None, {}
    return channel, decrypt_config(channel.config)


async def list_available_pay_types(
    session: AsyncSession, cache: CacheClient | None = None, code_id: int = 0
) -> list[dict]:
    """收银台支付方式列表：启用且有可用通道的支付方式。

    `code_id` 指定时只看该通道编码内的绑定通道：编码停用 / 成员全关的支付方式
    直接隐藏，否则买家会看到一个点了必然失败的支付方式。
    """
    types = (await session.exec(
        select(PaymentType).where(PaymentType.status == 1).order_by(PaymentType.sort, PaymentType.id)
    )).all()
    if not types:
        return []
    if code_id:
        code = await session.get(ChannelCode, code_id)
        if not code or code.status != 1:
            return []
        channel_rows = (await session.exec(
            select(Channel.payment_type_id)
            .join(ChannelCodeItem, col(ChannelCodeItem.channel_id) == col(Channel.id))
            .where(
                col(ChannelCodeItem.code_id) == code_id,
                col(ChannelCodeItem.status) == 1,
                Channel.status == 1,
            )
        )).all()
    else:
        channel_rows = (await session.exec(
            select(Channel.payment_type_id).where(Channel.status == 1)
        )).all()
    with_channel = set(channel_rows)
    return [
        {"id": t.id, "code": t.code, "name": t.name, "icon": t.icon}
        for t in types if t.id in with_channel
    ]
