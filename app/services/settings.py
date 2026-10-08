"""运行期支付设置（settings KV 表）。

与 config.py/.env 的分工：
- .env（部署期）：DB/Redis/APP_KEY/JWT/HTTP 超时
- settings 表（运行期即时生效）：站点、订单过期、通知阶梯、补单窗口、默认费率...

合法 key / 类型 / 默认值 / 分组的唯一事实来源见 app/constants/settings.py。
读取带 Redis 缓存（60s TTL），写入主动失效。

兼容性：get_all_settings 的值为注册表类型(int/bool/list[int]/str)；
部署后 60s 内可能读到旧缓存的字符串值，get_notify_ladder /
setting_enabled 均做了双形态兼容。
"""
import base64
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.constants.settings import PUBLIC_FEATURE_KEYS, SETTINGS_REGISTRY
from app.core.cache import CacheClient
from app.core.settings import default_map, validate_updates
from app.models import Setting

CACHE_KEY = "pay_settings"
CACHE_TTL = 60


def _cache_key() -> str:
    from app.core.config import config
    return f"{config.CACHE_PREFIX}{CACHE_KEY}" if config.CACHE_PREFIX else CACHE_KEY


async def get_all_settings(session: AsyncSession, cache: CacheClient | None = None) -> dict[str, Any]:
    """取全部运行期设置（合并默认值，值为注册表类型），带缓存。"""
    if cache is not None:
        try:
            cached = await cache.get(_cache_key())
            if cached:
                merged = default_map()
                merged.update(json.loads(cached))
                return merged
        except Exception:
            pass

    rows = (await session.exec(select(Setting))).all()
    stored = {row.key: row.value for row in rows if row.value is not None}
    merged = default_map()
    merged.update(stored)

    if cache is not None:
        try:
            await cache.set(_cache_key(), json.dumps(stored, ensure_ascii=False), ex=CACHE_TTL)
        except Exception:
            pass
    return merged


async def ensure_platform_rsa_key(session: AsyncSession) -> bool:
    """平台 RSA 私钥为空时自动生成并落库（对齐老系统 common.php 启动自检）。

    - 已配置（非空）返回 False，不覆盖；非空但非法的值同样保留，由站点配置页提示
    - 私钥格式与手动生成一致：PKCS#8 DER 的 Base64（platform_rsa_private_key 存储格式）
    - 多实例并发启动时唯一键冲突视为其它实例已生成，回滚后返回 False
    """
    settings = await get_all_settings(session)
    if str(settings.get("platform_rsa_private_key") or "").strip():
        return False

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    value = base64.b64encode(der).decode()
    try:
        await set_settings(session, {"platform_rsa_private_key": value})
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def get_setting(session: AsyncSession, key: str, default: Any = None,
                      cache: CacheClient | None = None) -> Any:
    """取单个设置（注册表类型）；缺行且 key 未注册时返回 default。"""
    settings = await get_all_settings(session, cache)
    if key in settings:
        return settings[key]
    return default


async def get_int_setting(session: AsyncSession, key: str, cache: CacheClient | None = None) -> int:
    value = await get_setting(session, key, cache=cache)
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(default_map().get(key, 0))
        except (TypeError, ValueError):
            return 0


async def get_money_setting(session: AsyncSession, key: str, cache: CacheClient | None = None) -> Decimal:
    """金额类设置 → Decimal（JSON 里存的是字符串，禁 float）。"""
    value = await get_setting(session, key, cache=cache)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        try:
            return Decimal(str(default_map().get(key, "0")))
        except Exception:
            return Decimal("0")


def setting_enabled(value: Any) -> bool:
    """布尔类设置的宽容读取：兼容 bool 与历史缓存里的 "1"/"0" 字符串。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true")


def build_public_features(settings: dict[str, Any]) -> dict[str, Any]:
    """从 get_all_settings 结果构建公开功能开关（白名单见 PUBLIC_FEATURE_KEYS）。

    布尔键经 setting_enabled（兼容历史缓存字符串），字符串键 trim。
    该结果未登录可读，绝不整体下发音。
    """
    features: dict[str, Any] = {}
    for key in PUBLIC_FEATURE_KEYS:
        value = settings.get(key)
        if key in ("site_qq_group", "site_app_url"):
            features[key] = str(value or "").strip()
        else:
            features[key] = setting_enabled(value)
    return features


async def get_public_features(session: AsyncSession, cache: CacheClient | None = None) -> dict[str, Any]:
    """/site-info 公开下发的功能开关（商户端菜单显隐）。"""
    settings = await get_all_settings(session, cache)
    return build_public_features(settings)


async def set_settings(session: AsyncSession, values: dict[str, Any],
                       cache: CacheClient | None = None) -> None:
    """整体校验后写入（值未变不刷新 updated_at）；任何 key 非法/类型不符
    抛 ValueError 且不产生部分写入。"""
    coerced, errors = validate_updates(values)
    if errors:
        raise ValueError("; ".join(errors))

    rows = (await session.exec(
        select(Setting).where(Setting.key.in_(list(coerced)))
    )).all()
    by_key = {row.key: row for row in rows}

    # 显式赋值 updated_at:sqlite 测试环境剥离了 ON UPDATE CURRENT_TIMESTAMP
    defaults = default_map()
    now = datetime.now()
    changed = False
    for key, value in coerced.items():
        row = by_key.get(key)
        if row is not None:
            if row.value == value:
                continue  # 值未变不刷新 updated_at
            row.value = value
            row.updated_at = now
        else:
            if value == defaults.get(key):
                continue  # 缺行且值等于默认值:不入库,保持「库里只存被改过的 key」语义
            row = Setting(key=key, value=value, updated_at=now)
        session.add(row)
        changed = True

    if changed:
        await session.commit()
    await invalidate_cache(cache)


async def invalidate_cache(cache: CacheClient | None = None) -> None:
    if cache is None:
        return
    try:
        await cache.delete(_cache_key())
    except Exception:
        pass


def get_notify_ladder(settings: dict[str, Any] | None = None) -> list[int]:
    """通知重试阶梯 → [1,2,5,10,30,60,120]。

    兼容两种形态：注册表类型 list[int]，以及部署后 60s 内旧缓存的 CSV 字符串。
    """
    raw = (settings or default_map()).get("notify_retry_ladder")
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    elif isinstance(raw, str):
        parts = raw.split(",")
    else:
        parts = []

    ladder = []
    for part in parts:
        try:
            v = int(part.strip())
            if v > 0:
                ladder.append(v)
        except (ValueError, AttributeError):
            continue
    return ladder or [1, 2, 5, 10, 30, 60, 120]
