"""运行期设置读写辅助 — 注册表(app/constants/settings.py)的校验/收敛纯函数。

全部为纯函数(不碰 DB);DB 交互在 app/services/settings.py。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from app.constants.settings import (
    ADMIN_SETTING_KEYS,
    SETTINGS_REGISTRY,
    SettingSpec,
    SettingType,
)
from app.models import Setting


def coerce_value(spec: SettingSpec, value: Any) -> Any:
    """按注册表类型收敛值(返回 JSON 可存储的形态),不合法抛 ValueError。

    注意 bool 是 int 的子类,INT/FLOAT/LIST_INT 必须先排除 bool。
    DECIMAL 收敛为 2 位小数字符串(JSON 存 str,读端转 Decimal,禁 float)。
    """
    t = spec.type
    if t == SettingType.BOOL:
        if isinstance(value, bool):
            return value
        raise ValueError("must be a boolean")
    if t == SettingType.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("must be an integer")
        return value
    if t == SettingType.FLOAT:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        return float(value)
    if t == SettingType.DECIMAL:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("must be a decimal string like \"1.00\"")
        try:
            d = Decimal(str(value).strip())
        except InvalidOperation as e:
            raise ValueError("must be a decimal string like \"1.00\"") from e
        if not d.is_finite():
            raise ValueError("must be a finite decimal")
        return str(d.quantize(Decimal("0.01")))
    if t in (SettingType.STRING, SettingType.TEXT):
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value
    if t == SettingType.LIST:
        if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
            raise ValueError("must be a list of strings")
        return value
    if t == SettingType.LIST_INT:
        if not isinstance(value, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) for i in value
        ):
            raise ValueError("must be a list of integers")
        return value
    raise ValueError(f"unsupported setting type: {t}")  # pragma: no cover


def validate_updates(updates: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """整体校验一次提交。errors 非空时 coerced 不可信,调用方必须整体拒绝。"""
    coerced: dict[str, Any] = {}
    errors: list[str] = []
    for key, value in updates.items():
        spec = SETTINGS_REGISTRY.get(key)
        if spec is None:
            errors.append(f"unknown setting key: {key}")
            continue
        try:
            coerced[key] = coerce_value(spec, value)
        except ValueError as e:
            errors.append(f"{key}: {e}")
    return coerced, errors


def default_map() -> dict[str, Any]:
    """注册表全量默认值(JSON 存储形态)。"""
    return {key: spec.default for key, spec in SETTINGS_REGISTRY.items()}


def merged_items(rows: list[Setting]) -> list[dict[str, Any]]:
    """DB 行 + 注册表合并为设置页配置项列表(按注册表顺序,前端按此渲染)。

    仅返回 ADMIN_SETTING_KEYS 白名单内的项(站点名称/趋势图与代理开关/扣量/结算);
    缺行补默认值,updated_at=None 表示「从未修改」。
    """
    by_key = {row.key: row for row in rows}
    items: list[dict[str, Any]] = []
    for key, spec in SETTINGS_REGISTRY.items():
        if key not in ADMIN_SETTING_KEYS:
            continue
        row = by_key.get(key)
        items.append({
            "key": key,
            "value": row.value if row is not None else spec.default,
            "default": spec.default,
            "type": str(spec.type),
            "group": str(spec.group),
            "label": spec.label,
            "description": spec.description,
            "updated_at": row.updated_at if row is not None else None,
        })
    return items
