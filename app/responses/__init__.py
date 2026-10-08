import re
from datetime import datetime
from typing import Any, Optional

from fastapi import status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


date_format = "%Y-%m-%d %H:%M:%S"
_iso_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')


def _format_datetime_value(value: Any) -> Any:
    """递归处理 datetime/ISO字符串，格式化为 YYYY-MM-DD HH:MM:SS（去掉 T）"""
    if isinstance(value, datetime):
        return value.strftime(date_format)
    if isinstance(value, str) and _iso_pattern.match(value):
        return value.replace('T', ' ')
    if isinstance(value, dict):
        return {k: _format_datetime_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_format_datetime_value(v) for v in value]
    return value


custom_encoder = {datetime: lambda dt: dt.strftime(date_format)}


# ============================================================
# 对齐 Laravel suc() / err() 响应格式
# ============================================================
# suc(data):
#   - data 是 string → {"code": 0, "message": data}
#   - data 是 array/object → 直接返回 data 本身（不包 code/message）
#   - data 是 LengthAwarePaginator → 分页结构
#
# err(code, message, errors, status):
#   - {"code": int, "message": str, "errors"?: array}
# ============================================================


def _safe_encode(items: list) -> list:
    """确保 SQLModel/SQLAlchemy 对象序列化时 bool 字段正确"""
    result = []
    for item in items:
        if hasattr(item, 'model_validate'):
            # SQLModel 实例 → validate 后再序列化
            validated = item.__class__.model_validate(item)
            result.append(jsonable_encoder(validated, custom_encoder=custom_encoder))
        elif isinstance(item, dict):
            result.append(jsonable_encoder(item, custom_encoder=custom_encoder))
        else:
            result.append(item)
    return result


def suc_data(data: Any = None, message: str = "success") -> JSONResponse:
    """统一包络格式：{"code": 0, "message": ..., "data": ...}。"""
    return JSONResponse(status_code=200, content={
        "code": 0,
        "message": message,
        "data": jsonable_encoder(data, custom_encoder=custom_encoder) if data is not None else None,
    })


def suc(data: Any = None, additions: dict | None = None) -> JSONResponse:
    """内部 API 统一成功响应：{"code": 0, "message": ..., "data": ...}。

    - data 为字符串 → 作为 message（{"code":0,"message":"已保存","data":null}）
    - data 为对象/列表/分页结构 → 放入 data 字段
    分页结构含 items/total，items 内的 ORM 对象先经 _safe_encode 修正序列化。
    """
    if isinstance(data, str):
        return JSONResponse(status_code=200, content={
            "code": 0, "message": data or "success", "data": None,
        })

    if isinstance(data, dict) and _is_pagination(data):
        data = dict(data)
        if isinstance(data.get("items"), list):
            data["items"] = _safe_encode(data["items"])
        if additions:
            data = {**data, **additions}
        return JSONResponse(status_code=200, content={
            "code": 0, "message": "ok",
            "data": jsonable_encoder(data, custom_encoder=custom_encoder),
        })

    payload = jsonable_encoder(data, custom_encoder=custom_encoder)
    if additions and isinstance(payload, dict):
        payload = {**payload, **additions}
    return JSONResponse(status_code=200, content={
        "code": 0, "message": "ok", "data": payload,
    })


def err(code: int = 400, message: str = "error", errors: list | None = None,
        http_status: int = 400) -> JSONResponse:
    """
    对齐 Laravel err() 函数

    返回 {"code": int, "message": str, "errors"?: array}
    """
    content: dict[str, Any] = {"code": code, "message": message}
    if errors:
        content["errors"] = errors
    return JSONResponse(status_code=http_status, content=content)


def paginate(items: list, total: int, page: int, per_page: int,
             has_more_pages: bool = False, has_pages: bool = False,
             summary: dict | None = None) -> dict:
    """
    构造 Laravel LengthAwarePaginator 格式的分页数据

    字段名与 Laravel 完全一致:
      total, total_page, count, per_page, current_page, has_more_pages, has_pages, items
    summary: 可选的列表级汇总（如订单列表按当前筛选的笔数/金额合计）
    """
    total_page = (total + per_page - 1) // per_page if per_page > 0 else 1
    result = {
        "total": total,
        "total_page": total_page,
        "count": len(items),
        "per_page": per_page,
        "current_page": page,
        "has_more_pages": has_more_pages,
        "has_pages": has_pages,
        "items": items,
    }
    if summary is not None:
        result["summary"] = summary
    return result


def _is_pagination(data: dict) -> bool:
    """判断是否为分页数据结构"""
    return "items" in data and "total" in data


def paginated_query(session, query, page: int, per_page: int, dump_fn=None):
    """
    通用分页查询辅助函数（同步 Session，保留给工具脚本）

    Args:
        session: SQLModel Session
        query: 已经包含所有过滤条件的 select query
        page: 页码
        per_page: 每页数量
        dump_fn: 可选的序列化函数，用于处理每个 item

    Returns:
        分页数据字典 (Laravel LengthAwarePaginator 格式)
    """
    from sqlmodel import select, func
    count_query = select(func.count()).select_from(query.subquery())
    total_count = session.exec(count_query).one()

    items = session.exec(query.offset((page - 1) * per_page).limit(per_page)).all()

    if dump_fn:
        items = [dump_fn(item) for item in items]

    return paginate(
        items=items,
        total=total_count,
        page=page,
        per_page=per_page,
        has_more_pages=page * per_page < total_count,
        has_pages=total_count > per_page,
    )


async def paginated_query_async(session, query, page: int, per_page: int, dump_fn=None):
    """通用分页查询（AsyncSession）。"""
    from sqlmodel import func, select
    count_query = select(func.count()).select_from(query.subquery())
    total_count = (await session.exec(count_query)).one()

    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()

    if dump_fn:
        items = [dump_fn(item) for item in items]
    elif items and hasattr(items[0], "model_dump"):
        from fastapi.encoders import jsonable_encoder
        items = [jsonable_encoder(it.__class__.model_validate(it), custom_encoder=custom_encoder)
                 for it in items]

    return paginate(
        items=items,
        total=total_count,
        page=page,
        per_page=per_page,
        has_more_pages=page * per_page < total_count,
        has_pages=total_count > per_page,
    )


class FilesList(BaseModel):
    key: str
    size: int
    mime_type: Optional[str] = ''
    last_modified: Optional[datetime] = ''

    model_config = ConfigDict(
        extra='allow',
        arbitrary_types_allowed=True,
        from_attributes=True,
        json_encoders={datetime: lambda dt: dt.strftime(date_format)},
    )
