"""权限树对账 — PERMISSION_TREE 与数据库行的差异计算。

CLI（app/commands/permission.py）与后台「同步权限」接口共用这里的逻辑；
这里是异步实现，因为本项目的权限命令与路由都跑在 AsyncSession 上。

同步语义（本项目口径：以代码树为准）：
- 新增 / 更新：树里的节点补齐，名称或父节点变化就地更新；
- 删除：树里已不存在的权限连同管理员授权一并删除（支付网关没有自定义权限的场景）。
"""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import delete as sa_delete
from sqlmodel import select

from app.constants.permissions import PERMISSION_TREE
from app.models import SystemAdminPermission, SystemPermission


def tree_keys() -> set[str]:
    """代码树里的全部 key（含分组主节点）。"""
    keys = {group_key for _, group_key, _ in PERMISSION_TREE}
    keys.update(child_key for _, _, children in PERMISSION_TREE for _, child_key in children)
    return keys


def build_plan(rows: Iterable[SystemPermission]) -> dict:
    """对比权限树与现有行，返回 {"create": [...], "update": [...]}。"""
    rows = list(rows)
    by_key = {row.key: row for row in rows}
    name_by_id = {row.id: row.name for row in rows}
    next_id = max((row.id for row in rows), default=0) + 1

    create: list[dict] = []
    update: list[dict] = []

    for group_name, group_key, children in PERMISSION_TREE:
        group = by_key.get(group_key)
        if group is None:
            group_id = next_id
            next_id += 1
            create.append({
                "id": group_id, "pid": 0, "name": group_name, "key": group_key,
                "is_group": True,
                "group_key": group_key, "group_name": group_name,
                "before_name": None, "before_group_name": None,
                "name_changed": True, "group_changed": False,
            })
        else:
            group_id = group.id
            if group.name != group_name:
                update.append({
                    "id": group.id, "pid": group.pid, "name": group_name, "key": group_key,
                    "is_group": True,
                    "group_key": group_key, "group_name": group_name,
                    "before_name": group.name, "before_group_name": None,
                    "name_changed": True, "group_changed": False,
                })

        for child_name, child_key in children:
            child = by_key.get(child_key)
            if child is None:
                create.append({
                    "id": next_id, "pid": group_id, "name": child_name, "key": child_key,
                    "is_group": False,
                    "group_key": group_key, "group_name": group_name,
                    "before_name": None, "before_group_name": None,
                    "name_changed": True, "group_changed": False,
                })
                next_id += 1
            elif child.name != child_name or child.pid != group_id:
                update.append({
                    "id": child.id, "pid": group_id, "name": child_name, "key": child_key,
                    "is_group": False,
                    "group_key": group_key, "group_name": group_name,
                    "before_name": child.name,
                    "before_group_name": name_by_id.get(child.pid),
                    "name_changed": child.name != child_name,
                    "group_changed": child.pid != group_id,
                })

    return {"create": create, "update": update}


def stale_rows(rows: Iterable[SystemPermission]) -> list[SystemPermission]:
    """树里已不存在的行（会被删除，连同管理员授权）。"""
    keys = tree_keys()
    return [row for row in rows if row.key not in keys]


# 预览只暴露渲染需要的字段，id / pid 属于内部实现
_PREVIEW_FIELDS = (
    "key", "name", "is_group", "group_key", "group_name",
    "before_name", "before_group_name", "name_changed", "group_changed",
)


def preview_payload(plan: dict, rows: Iterable[SystemPermission]) -> dict:
    """把对账结果裁剪成前端可渲染的预览结构（含将被删除的节点）。"""
    created = [
        {"key": item["key"], "name": item["name"], "is_group": item["is_group"],
         "group_key": item["group_key"], "group_name": item["group_name"]}
        for item in plan["create"]
    ]
    updated = [
        {field: item[field] for field in _PREVIEW_FIELDS}
        for item in plan["update"]
    ]
    removed = [{"key": row.key, "name": row.name} for row in stale_rows(rows)]
    return {
        "created": created,
        "updated": updated,
        "removed": removed,
        "created_count": len(created),
        "updated_count": len(updated),
        "removed_count": len(removed),
    }


async def apply_sync(session) -> dict:
    """按代码树同步数据库（异步），返回 {created, updated, removed}。"""
    rows = (await session.exec(select(SystemPermission))).all()
    plan = build_plan(rows)
    stale = stale_rows(rows)

    for item in plan["create"]:
        session.add(SystemPermission(
            id=item["id"], pid=item["pid"], name=item["name"], key=item["key"]
        ))
    for item in plan["update"]:
        row = await session.get(SystemPermission, item["id"])
        if row is None:  # 预览与执行之间被别的进程删掉，跳过即可
            continue
        row.name = item["name"]
        row.pid = item["pid"]

    if stale:
        stale_ids = [row.id for row in stale]
        await session.exec(
            sa_delete(SystemAdminPermission).where(
                SystemAdminPermission.permission_id.in_(stale_ids)
            )
        )
        for row in stale:
            await session.delete(row)

    await session.commit()
    return {
        "created": len(plan["create"]),
        "updated": len(plan["update"]),
        "removed": len(stale),
    }
