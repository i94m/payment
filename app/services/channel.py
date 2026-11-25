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


def _epay_pick(pairs: list[tuple[Any, int]]) -> Any | None:
    """加权随机抽签——epay `Channel::random_weight` 的逐行移植（`includes/lib/Channel.php`）：

    ```php
    $weightSum = 0;
    foreach ($arr as $value) $weightSum += intval($value['weight']);
    if ($weightSum <= 0) return false;
    $randNum = mt_rand(1, $weightSum);
    foreach ($arr as $v) {
        if ($randNum <= $v['weight']) return $v['name'];
        $randNum -= $v['weight'];
    }
    ```

    - 在整数区间 `[1, 权重和]` 取点（含首尾），按 `pairs` 顺序累减；命中概率严格等于
      `weight / 权重和`，权重 0 永远命中不了（区间不推进）。
    - 权重和 ≤ 0 返回 None（epay 返回 false = 不选通道），不是退化成等概率。
    - 负数权重由后台校验拦下；这里按 0 处理，避免脏数据把区间顶歪。
    """
    total = sum(max(int(w), 0) for _, w in pairs)
    if total <= 0:
        return None
    point = random.randint(1, total)
    for item, w in pairs:
        w = max(int(w), 0)
        if point <= w:
            return item
        point -= w
    return None


def _weighted_random(channels: list[Channel]) -> Channel:
    """通道级（默认路由）加权随机：一次 epay 抽签。

    通道侧没有「合计 100」的强校验，全零权重属于脏配置：沿用历史兜底（永远取最后一条），
    不在这里改成报错，避免一条漏配权重的通道把整条支付方式打挂。
    """
    pick = _epay_pick([(c, c.weight) for c in channels])
    return pick if pick is not None else channels[-1]


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


# ---- 编码级单笔金额区间（0 = 不限，两端都 0 = 不限制） ----

def amount_in_range(money: Decimal, min_amount: Decimal, max_amount: Decimal) -> bool:
    """金额是否落在 [min, max]（两端含）。0 表示该端不限。"""
    if min_amount and min_amount > 0 and money < min_amount:
        return False
    if max_amount and max_amount > 0 and money > max_amount:
        return False
    return True


def code_amount_ok(code: ChannelCode, money: Decimal) -> bool:
    """订单申报金额是否落在编码的限额区间内。"""
    return amount_in_range(
        dmoney(money),
        dmoney(code.min_amount or 0),
        dmoney(code.max_amount or 0),
    )


def code_amount_text(code: ChannelCode) -> str:
    """限额展示文案（不含单位）：`1.00 ~ 9.00` / `≥ 5.00` / `≤ 100.00` / `不限`。

    后台列表与商户端共用同一口径，避免两边各写一套把「0=不限」解释错。
    """
    lo = dmoney(code.min_amount or 0)
    hi = dmoney(code.max_amount or 0)
    if lo <= 0 and hi <= 0:
        return "不限"
    if hi <= 0:
        return f"≥ {lo}"
    if lo <= 0:
        return f"≤ {hi}"
    return f"{lo} ~ {hi}"


def code_amount_error(code: ChannelCode) -> str:
    """限额不通过时给下游的文案：说清区间，而不是笼统的「当前不可用」。"""
    lo = dmoney(code.min_amount or 0)
    hi = dmoney(code.max_amount or 0)
    if hi <= 0:
        return f"通道编码 {code.code} 单笔金额需不低于 {lo} 元"
    if lo <= 0:
        return f"通道编码 {code.code} 单笔金额需不高于 {hi} 元"
    return f"通道编码 {code.code} 单笔金额需在 {lo} ~ {hi} 元之间"


async def default_channel_payload(session: AsyncSession) -> dict[str, Any] | None:
    """平台默认通道的对接信息口径（`channels.is_default`，全平台唯一，只做展示）。

    商户端 `/merchant-api/account/apikey` 与后台商户详情共用这一处，保证两边逐字一致。

    - 停用的通道不算（后台停用时会清标记，这里再判一次兜底），没有则返回 None
    - `codes` = 绑定该通道的**启用中**编码，按编码升序：对接信息展示的是下游能直接
      当 `channel_code` 传进去的编码，不是通道名称（通道可以被多个编码引用，
      所以给的是列表；前端用「、」拼起来展示，为空时回退到通道名称）
    """
    default = (await session.exec(
        select(Channel)
        .where(col(Channel.is_default).is_(True), col(Channel.status) == 1)
        .order_by(col(Channel.id))
        .limit(1)
    )).first()
    if not default:
        return None
    pt = await session.get(PaymentType, default.payment_type_id)
    codes = (await session.exec(
        select(ChannelCode.code)
        .join(ChannelCodeItem, col(ChannelCodeItem.code_id) == col(ChannelCode.id))
        .where(
            ChannelCodeItem.channel_id == default.id,
            col(ChannelCode.status) == 1,
        )
        .order_by(col(ChannelCode.code))
    )).all()
    return {
        "name": default.name,
        "payment_type": pt.name if pt else "",
        "codes": list(codes),
    }


def effective_merchant_rate(merchant_rate: Decimal | None) -> Decimal | None:
    """生效商户费率：商户专属费率，没有则返回 None（= 用通道自身费率兜底）。

    `process_payment` 把返回值原样透传给选通道路由：返回 None 时由被选中通道的
    `merchant_rate` 兜底。编码级费率已下线（迁移 0020），不再参与这一级。
    """
    if merchant_rate is not None:
        return dmoney(merchant_rate)
    return None


async def code_supports_type(session: AsyncSession, code_id: int, payment_type_id: int) -> bool:
    """编码下是否有与该支付方式绑定的通道（只看绑定关系，不看通道运行时状态）。

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
    """按权重不放回抽签：每一步都是一次**完整的 epay 抽签**（`_epay_pick`）。

    epay 每单只抽一次，抽中的通道不可用就整单失败；本项目在抽中之后还要原子占坑抢限速
    名额，抢不到需要换下一条，所以把「一次抽签」重复到池子抽空。逐步抽签与 epay 的
    「先剔除不可用、再在剩余集合上抽签」分布等价（剔除后相对权重不变），因此：

    - 第一个元素就是 epay 那一抽，概率严格等于 `weight / 权重和`；
    - 后续元素也只是在剩余集合上按同一口径重抽，不会越出按权重分流的比例。
    """
    pool = list(pairs)
    ordered: list[Any] = []
    while pool:
        pick = _epay_pick(pool)
        if pick is None:
            # 防御：正权重在抽完前不会被删光，走到这里说明入参异常。
            # 不退化成随机：静默换策略会让运营以为按权重分流，实际全量随机。
            break
        ordered.append(pick)
        pool = [it for it in pool if it[0] is not pick]
    return ordered


def _order_code_candidates(
    available: list[Channel], mode: int, item_map: dict[int, ChannelCodeItem]
) -> list[Channel]:
    """按**编码级** mode 排定候选：0=随机 / 2=权重。

    轮询（旧 mode=1）已下线：后台不再提供该策略，迁移 0019 把存量编码改为随机；
    万一库里还留着 mode=1 的行，这里走随机分支而不是报错，编码不会因为旧值不可用。

    权重 0 = **不参与分流**（与 epay 轮询组加权随机同口径）：既不做首选，也不做限速
    回退候选。正权重成员全部不可用时返回空列表，由调用方报「编码当前不可用」，
    不退化成随机或默认路由 —— 编码代表费率/成本边界，静默改策略比报错更难追。
    """
    if mode == 2:
        # 抽签顺序 = 后台成员表的顺序（ChannelCodeItem.sort），与 epay 轮询组 `通道:权重`
        # 串的顺序一致：分界点可以按配置顺序核对（1~50 命中第一行、51~80 命中第二行…）。
        members = sorted(
            (c for c in available if item_map[c.id].weight > 0),
            key=lambda c: (item_map[c.id].sort, c.id),
        )
        return _weighted_order([(c, item_map[c.id].weight) for c in members])
    pool = list(available)
    random.shuffle(pool)
    return pool


def _resolve_by_code(chosen: Channel, merchant_rate: Decimal | None) -> ResolvedChannel:
    """编码选定通道后组装结果。

    轮询下线后编码内选路不再需要绑定行游标（`channel_code_items.last_used_at` 保留字段、
    当前不读写），所以这里不写库，只做费率解析与凭证解密。
    """
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

    与 choose_channel 的差别只有两处：候选集来自 `channel_code_items`（只看本编码绑定的
    成员通道 + 通道全局启用 + 该支付方式），策略取自编码主档的 mode。
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
            return _resolve_by_code(c, merchant_rate)
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

    `code_id` 指定时只看该通道编码内绑定的通道：编码停用 / 编码内通道全部不可用的
    支付方式直接隐藏，否则买家会看到一个点了必然失败的支付方式。
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
