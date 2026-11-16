"""后台敏感操作审计 — 谁 / 做了什么 / 对哪个对象 / 前后值 / 何时 / 从哪来。

设计要点：
- **只增不改**：审计行与业务行同一事务提交，写失败只打日志，绝不影响业务
  （`record_audit` 内部吞异常）——审计是观察手段，不是业务闸门。
- **前后值脱敏**：`_scrub` 按字段名（password/secret/key/token...）把值替换为
  `******`，并截断长文本，避免把凭证/私钥写进审计表。
- **调用约定**：在业务提交之后调用（`commit=True` 默认自带提交），
  这样「已产生效果」的操作才留痕；失败返回的请求不记录。
"""
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import AuthResult
from app.models import SystemAuditLog

_log = logging.getLogger(__name__)

# 字段名命中这些片段即视为敏感值（大小写不敏感）
_SECRET_HINTS = ("password", "passwd", "secret", "private", "key", "token", "sign", "credential")
_MASK = "******"
_MAX_TEXT = 512  # 单值最大长度
_MAX_DEPTH = 4   # 嵌套深度上限（超过直接转字符串）


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def _jsonable(value: Any, depth: int = 0) -> Any:
    """转成可 JSON 序列化的形态；Decimal → 字符串、时间 → ISO 字符串。"""
    if depth > _MAX_DEPTH:
        return str(value)[:_MAX_TEXT]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, str):
        return value if len(value) <= _MAX_TEXT else value[:_MAX_TEXT] + "…"
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in list(value)[:50]]
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in list(value.items())[:50]}
    return str(value)[:_MAX_TEXT]


def _scrub(data: Any, depth: int = 0) -> Any:
    """脱敏：敏感字段名 → `******`，其余递归截断。"""
    if depth > _MAX_DEPTH:
        return _MASK
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in list(data.items())[:50]:
            key_str = str(key)
            if _is_secret_key(key_str):
                out[key_str] = _MASK if value not in (None, "") else value
            else:
                out[key_str] = _scrub(value, depth + 1)
        return out
    if isinstance(data, (list, tuple, set)):
        return [_scrub(v, depth + 1) for v in list(data)[:50]]
    return _jsonable(data)


def actor_of(actor: AuthResult | None) -> tuple[str, int, str]:
    """AuthResult → (guard, id, 展示名)。无 actor 视为系统操作。"""
    if actor is None:
        return "system", 0, "system"
    user = actor.user
    name = getattr(user, "name", None) or getattr(user, "username", None) or ""
    return actor.guard, int(getattr(user, "id", 0) or 0), str(name)[:64]


def request_meta(request: Any) -> tuple[str | None, str | None]:
    """请求 → (ip, user_agent)，不做任何信任代理头的推断（与业务日志同口径）。"""
    if request is None:
        return None, None
    ip = request.client.host if getattr(request, "client", None) else None
    ua = ""
    try:
        ua = request.headers.get("user-agent", "") or ""
    except Exception:
        ua = ""
    return (ip[:45] if ip else None), (ua[:255] or None)


async def record_audit(
    session: AsyncSession,
    *,
    action: str,
    actor: AuthResult | None = None,
    target_type: str = "",
    target_id: str | int = "",
    before: dict | None = None,
    after: dict | None = None,
    request: Any = None,
    remark: str = "",
    commit: bool = True,
) -> SystemAuditLog | None:
    """写一条审计。返回值仅便于测试断言；任何异常都被吞掉（只打日志）。"""
    try:
        guard, actor_id, actor_name = actor_of(actor)
        ip, user_agent = request_meta(request)
        row = SystemAuditLog(
            actor_guard=guard,
            actor_id=actor_id,
            actor_name=actor_name,
            action=str(action)[:64],
            target_type=str(target_type)[:32],
            target_id=str(target_id)[:64],
            before=_scrub(before) if before is not None else None,
            after=_scrub(after) if after is not None else None,
            ip=ip,
            user_agent=user_agent,
            remark=(remark or "")[:255] or None,
        )
        session.add(row)
        if commit:
            await session.commit()
            await session.refresh(row)
        return row
    except Exception:
        _log.warning("写审计失败 action=%s target=%s/%s", action, target_type, target_id,
                     exc_info=True)
        return None


def changed_pairs(before: dict | None, after: dict | None) -> tuple[dict, dict]:
    """只保留有变化的键，返回可直接落审计的 (before, after) 快照。

    值比较统一走 str()：Decimal("1.00") 与 "1.00" 视为未变，避免伪变更刷屏。
    """
    before = before or {}
    after = after or {}
    keys = [k for k in set(before) | set(after)
            if str(before.get(k)) != str(after.get(k))]
    return ({k: before.get(k) for k in keys}, {k: after.get(k) for k in keys})
