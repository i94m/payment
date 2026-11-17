"""后台首页快捷配置（金额路由 / 通道限速）。

存储复用 settings KV 表，但使用三个专用 key、不经 SETTINGS_REGISTRY 通用渲染
（它们是按支付方式/通道 ID 展开的动态配置，注册表只收静态 key）:
- payroute_config:        {"<payment_type_id>": {enable, min, max, channel}}
- channel_ratelimit:      {"<channel_id>": 次/分钟}
- channel_ratelimit_last: int — 限速面板最后选中通道（浏览器 localStorage 缺失时回退）

读取不做缓存（每单一次 KV 查，量级可忽略）；写入即时生效。
"""
import math
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import case, func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Order, Setting

KEY_PAYROUTE = "payroute_config"
KEY_RATELIMIT = "channel_ratelimit"
KEY_RATELIMIT_LAST = "channel_ratelimit_last"


async def _read(session: AsyncSession, key: str):
    row = (await session.exec(select(Setting).where(Setting.key == key))).first()
    return row.value if row is not None else None


async def _write(session: AsyncSession, key: str, value) -> None:
    """UPSERT 单个 KV 行（显式赋 updated_at，兼容 sqlite 测试环境）。"""
    row = (await session.exec(select(Setting).where(Setting.key == key))).first()
    if row is None:
        session.add(Setting(key=key, value=value, created_at=datetime.now(), updated_at=datetime.now()))
    else:
        row.value = value
        row.updated_at = datetime.now()
    await session.commit()


# ============================================================
# 金额路由 payroute_config
# ============================================================

def normalize_route_item(item: dict) -> dict:
    """收敛单个支付方式的路由配置为规范形态（金额两位小数字符串）。"""
    minv = Decimal(str(item.get("min") or 0))
    maxv = Decimal(str(item.get("max") or 0))
    channel = int(item.get("channel") or 0)
    enable = 1 if item.get("enable") in (1, True, "1") else 0
    return {
        "enable": enable,
        "min": str(minv.quantize(Decimal("0.01"))),
        "max": str(maxv.quantize(Decimal("0.01"))),
        "channel": channel,
    }


async def get_payroute_config(session: AsyncSession) -> dict[int, dict]:
    """取全部金额路由配置，key 收敛为 int 支付方式 ID，值经 normalize。"""
    raw = await _read(session, KEY_PAYROUTE)
    if not isinstance(raw, dict):
        return {}
    result: dict[int, dict] = {}
    for k, item in raw.items():
        try:
            tid = int(k)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        result[tid] = normalize_route_item(item)
    return result


async def set_payroute_items(session: AsyncSession, items: dict[int, dict]) -> None:
    """批量合并写入多个支付方式的路由配置（一次读写，避免逐项覆盖丢失更新）。"""
    if not items:
        return
    raw = await _read(session, KEY_PAYROUTE)
    raw = dict(raw) if isinstance(raw, dict) else {}
    for type_id, item in items.items():
        raw[str(int(type_id))] = normalize_route_item(item)
    await _write(session, KEY_PAYROUTE, raw)


async def set_payroute_item(session: AsyncSession, type_id: int, item: dict) -> None:
    """合并写入单个支付方式的路由配置。"""
    await set_payroute_items(session, {int(type_id): item})


# ============================================================
# 通道限速 channel_ratelimit / channel_ratelimit_last
# ============================================================

async def get_ratelimit_map(session: AsyncSession) -> dict[int, int]:
    """{通道ID: 次/分钟}，跳过非法/非正值。"""
    raw = await _read(session, KEY_RATELIMIT)
    if not isinstance(raw, dict):
        return {}
    result: dict[int, int] = {}
    for k, v in raw.items():
        try:
            cid, lim = int(k), int(v)
        except (TypeError, ValueError):
            continue
        if cid > 0 and lim > 0:
            result[cid] = lim
    return result


async def replace_ratelimit_map(session: AsyncSession, mapping: dict[int, int]) -> None:
    """整体覆盖限速映射；空 dict 即「一键关闭所有通道限速」。"""
    await _write(session, KEY_RATELIMIT, {str(cid): int(lim) for cid, lim in mapping.items() if cid > 0 and lim > 0})


async def set_ratelimit_one(session: AsyncSession, channel_id: int, value: int) -> None:
    """设置/移除单个通道限速（value<=0 移除该通道）。"""
    mapping = await get_ratelimit_map(session)
    if value > 0:
        mapping[channel_id] = int(value)
    else:
        mapping.pop(channel_id, None)
    await replace_ratelimit_map(session, mapping)


async def get_ratelimit_last(session: AsyncSession) -> int:
    try:
        return int(await _read(session, KEY_RATELIMIT_LAST) or 0)
    except (TypeError, ValueError):
        return 0


async def set_ratelimit_last(session: AsyncSession, channel_id: int) -> None:
    await _write(session, KEY_RATELIMIT_LAST, int(channel_id) if channel_id > 0 else 0)


# ============================================================
# 限速判定（供通道路由 choose_channel 使用）
# ============================================================

async def exceeded_channel_ids(session: AsyncSession, ratelimit_map: dict[int, int],
                               now: datetime | None = None) -> set[int]:
    """计算当前已达速率上限的通道集合。双条件平滑：

    ①近60秒总单数 >= ratelimit_<通道ID>；②近5秒单数 >= ceil(速率/12)。
    ②把突发摊匀到每5秒一档，避免前段冲满后段全挡；60秒总量仍由①封顶。
    """
    if not ratelimit_map:
        return set()
    now = now or datetime.now()
    since60 = now - timedelta(seconds=60)
    since5 = now - timedelta(seconds=5)
    ids = list(ratelimit_map.keys())
    rows = (await session.exec(
        select(
            Order.channel_id,
            func.count().label("cnt60"),
            func.sum(case((Order.created_at >= since5, 1), else_=0)).label("cnt5"),
        )
        .where(col(Order.channel_id).in_(ids), Order.created_at >= since60)
        .group_by(Order.channel_id)
    )).all()

    exceeded: set[int] = set()
    for row in rows:
        cid, cnt60, cnt5 = int(row[0]), int(row[1] or 0), int(row[2] or 0)
        lim = ratelimit_map.get(cid, 0)
        if lim <= 0:
            continue
        cap5 = math.ceil(lim / 12)
        if cnt60 >= lim or cnt5 >= cap5:
            exceeded.add(cid)
    return exceeded


def money_ok(min_str: str, max_str: str, money: Decimal) -> bool:
    """金额路由区间判断（两端含）。min/max 任一 <=0 视为不启用。"""
    try:
        minv, maxv = Decimal(str(min_str)), Decimal(str(max_str))
    except (InvalidOperation, TypeError, ValueError):
        return False
    if minv <= 0 or maxv <= 0 or money <= 0:
        return False
    return money >= minv and money <= maxv
