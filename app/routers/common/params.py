"""筛选/分页参数解析 —— 非法输入返回 400，而不是 500。

列表接口的过滤条件多来自前端 DataList 的 JSON body 或 query string，
旧实现直接 `int(body["status"])`：传 `?merchant_id=abc`、`1.5`、`true`
会抛 ValueError/TypeError，被全局兜底处理器转成「系统内部错误」500，
前端只能看到系统异常而不知道是哪个筛选条件写错了。

这里统一在入口转换并给出 400 + 具体字段提示。`minimum`/`maximum`
按旧行为做区间**收敛**（分页页码等场景传大了应回落到边界，不是报错）。
"""
import re

from fastapi import HTTPException

_INT_RE = re.compile(r"[+-]?\d+")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _bad(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": 400, "message": message})


def _clamp(value: int, minimum: int | None, maximum: int | None) -> int:
    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def parse_int(value, field: str, *, default: int | None = None,
              minimum: int | None = None, maximum: int | None = None) -> int | None:
    """把 body/query 里的任意值转成 int。

    - None / 空白字符串 → `default`
    - 整数、整数值浮点、纯整数字符串（`"12"` / `"-3"`）→ int
    - 其余（`"abc"` / `"1.5"` / `"1e3"` / 列表…）→ 400，提示 `field`
    - `minimum`/`maximum`：越界时收敛到边界（沿用分页参数旧行为）
    """
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        if not _INT_RE.fullmatch(text):
            raise _bad(field, f"参数 {field} 必须为整数")
        result = int(text)
    elif isinstance(value, bool):
        result = int(value)
    elif isinstance(value, int):
        result = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise _bad(field, f"参数 {field} 必须为整数")
        result = int(value)
    else:
        raise _bad(field, f"参数 {field} 必须为整数")
    return _clamp(result, minimum, maximum)


def parse_bool(value, field: str, *, default: bool | None = None) -> bool | None:
    """把 body/query 里的任意值转成 bool（兼容 0/1、true/false、yes/no）。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise _bad(field, f"参数 {field} 必须为布尔值")
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return default
        if text in _TRUE_VALUES:
            return True
        if text in _FALSE_VALUES:
            return False
    raise _bad(field, f"参数 {field} 必须为布尔值")
