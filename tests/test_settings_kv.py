"""运行期设置 KV 化：注册表校验 / typed 读取 / updated_at 语义 / 兼容形态。"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlmodel import select

from app.constants.settings import SETTINGS_REGISTRY, SettingSpec, SettingType
from app.core.settings import coerce_value, default_map, validate_updates
from app.models import Setting
from app.services.settings import (
    get_all_settings,
    get_int_setting,
    get_money_setting,
    get_notify_ladder,
    set_settings,
    setting_enabled,
)


def _spec(key: str) -> SettingSpec:
    return SETTINGS_REGISTRY[key]


# ---- 注册表纯函数 ----

def test_coerce_bool_rejects_string_and_int():
    with pytest.raises(ValueError):
        coerce_value(_spec("query_unpaid_enable"), "1")
    with pytest.raises(ValueError):
        coerce_value(_spec("query_unpaid_enable"), 1)  # bool 是 int 子类,必须拒
    assert coerce_value(_spec("query_unpaid_enable"), True) is True


def test_coerce_int_rejects_bool_and_str():
    with pytest.raises(ValueError):
        coerce_value(_spec("order_expire_minutes"), True)
    with pytest.raises(ValueError):
        coerce_value(_spec("order_expire_minutes"), "30")
    assert coerce_value(_spec("order_expire_minutes"), 45) == 45


def test_coerce_decimal_quantizes_to_string():
    assert coerce_value(_spec("settle_min_amount"), "1.5") == "1.50"
    assert coerce_value(_spec("settle_min_amount"), 2) == "2.00"
    with pytest.raises(ValueError):
        coerce_value(_spec("settle_min_amount"), "abc")


def test_coerce_list_int_rejects_str_elements_and_bools():
    assert coerce_value(_spec("notify_retry_ladder"), [1, 2]) == [1, 2]
    with pytest.raises(ValueError):
        coerce_value(_spec("notify_retry_ladder"), ["1", "2"])
    with pytest.raises(ValueError):
        coerce_value(_spec("notify_retry_ladder"), [True, 2])


def test_validate_updates_reports_unknown_and_type_errors():
    coerced, errors = validate_updates({"site_name": "ok", "nope": 1, "order_expire_minutes": "x"})
    assert errors == [
        "unknown setting key: nope",
        "order_expire_minutes: must be an integer",
    ]
    assert coerced == {"site_name": "ok"}


# ---- 服务层(DB) ----

async def test_get_all_settings_merges_typed_defaults(session):
    settings = await get_all_settings(session)
    assert settings["order_expire_minutes"] == 30
    assert settings["query_unpaid_enable"] is True
    assert settings["notify_retry_ladder"] == [1, 2, 5, 10, 30, 60, 120]
    assert settings["settle_min_amount"] == "1.00"


async def test_set_settings_validates_atomically(session):
    with pytest.raises(ValueError):
        await set_settings(session, {"site_name": "ok", "order_expire_minutes": "bad"})
    rows = (await session.exec(select(Setting))).all()
    assert rows == []  # 整体拒绝,合法 key 也不落库


async def test_set_settings_upserts_typed_values(session):
    await set_settings(session, {"site_name": "新站名", "order_expire_minutes": 15})
    settings = await get_all_settings(session)
    assert settings["site_name"] == "新站名"
    assert settings["order_expire_minutes"] == 15

    rows = {r.key: r for r in (await session.exec(select(Setting))).all()}
    assert rows["order_expire_minutes"].value == 15
    assert rows["order_expire_minutes"].updated_at is not None


async def test_set_settings_same_value_keeps_updated_at(session):
    old = datetime(2026, 1, 1, 8, 0, 0)
    session.add(Setting(key="site_name", value="same", updated_at=old))
    await session.commit()

    await set_settings(session, {"site_name": "same"})
    row = (await session.exec(select(Setting).where(Setting.key == "site_name"))).one()
    assert row.updated_at == old  # 值未变不刷新


async def test_get_int_and_money_settings(session):
    await set_settings(session, {"order_expire_minutes": 20, "settle_min_amount": "2.50"})
    assert await get_int_setting(session, "order_expire_minutes") == 20
    assert await get_money_setting(session, "settle_min_amount") == Decimal("2.50")
    # 脏值回退默认(行已存在,直接改 value 模拟)
    row = (await session.exec(select(Setting).where(Setting.key == "order_expire_minutes"))).one()
    row.value = "not-a-number"
    session.add(row)
    await session.commit()
    assert await get_int_setting(session, "order_expire_minutes") == 30


# ---- 兼容形态(部署后 60s 内旧缓存的字符串值) ----

def test_get_notify_ladder_accepts_list_and_legacy_csv():
    assert get_notify_ladder({"notify_retry_ladder": [2, 4, 8]}) == [2, 4, 8]
    assert get_notify_ladder({"notify_retry_ladder": "3,6,9"}) == [3, 6, 9]  # 旧缓存 CSV
    assert get_notify_ladder({"notify_retry_ladder": ""}) == [1, 2, 5, 10, 30, 60, 120]
    assert get_notify_ladder({}) == [1, 2, 5, 10, 30, 60, 120]


def test_setting_enabled_accepts_bool_and_legacy_str():
    assert setting_enabled(True) is True
    assert setting_enabled("1") is True
    assert setting_enabled("true") is True
    assert setting_enabled(False) is False
    assert setting_enabled("0") is False
    assert setting_enabled(None) is False
