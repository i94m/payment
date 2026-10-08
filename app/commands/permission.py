"""
权限同步命令 — 从代码定义同步权限到数据库

用法:
    uv run artisan.py permission sync
    uv run artisan.py permission sync --reset

权限树定义在 PERMISSION_TREE 列表中，修改后运行 sync 即可同步到数据库。
"""

import typer
from sqlalchemy import delete as sa_delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.constants.permissions import PERMISSION_TREE
from app.core.database import engine, session_maker
from app.models import SystemAdminPermission, SystemPermission

app = typer.Typer(help="管理权限")


async def _sync_tree(session: AsyncSession, reset: bool = False):
    """同步权限树到数据库（异步）

    树中新增/改名的权限 upsert 入库；已从树中移除的权限连同角色关联一并删除。
    """
    if reset:
        for perm in (await session.exec(select(SystemPermission))).all():
            await session.delete(perm)
        await session.commit()
        print("  已清空权限表")

    key_to_id: dict[str, int] = {}
    auto_id = 1

    existing = (await session.exec(select(SystemPermission))).all()
    for perm in existing:
        key_to_id[perm.key] = perm.id
        if perm.id >= auto_id:
            auto_id = perm.id + 1

    created_count = 0
    updated_count = 0

    tree_keys = {group_key for _, group_key, _ in PERMISSION_TREE}
    tree_keys.update(child_key for _, _, children in PERMISSION_TREE for _, child_key in children)

    # 删除树中已不存在的权限（连同角色关联）
    stale = [perm for perm in existing if perm.key not in tree_keys]
    if stale:
        await session.exec(sa_delete(SystemAdminPermission).where(
            SystemAdminPermission.permission_id.in_([perm.id for perm in stale])))
        for perm in stale:
            await session.delete(perm)
            key_to_id.pop(perm.key, None)
        removed_count = len(stale)
    else:
        removed_count = 0

    for group_name, group_key, children in PERMISSION_TREE:
        if group_key in key_to_id:
            pid = key_to_id[group_key]
            group_perm = await session.get(SystemPermission, pid)
            if group_perm and group_perm.name != group_name:
                group_perm.name = group_name
                updated_count += 1
        else:
            pid = auto_id
            auto_id += 1
            session.add(SystemPermission(id=pid, pid=0, name=group_name, key=group_key))
            key_to_id[group_key] = pid
            created_count += 1

        for child_name, child_key in children:
            if child_key in key_to_id:
                child_perm = await session.get(SystemPermission, key_to_id[child_key])
                if child_perm and (child_perm.name != child_name or child_perm.pid != pid):
                    child_perm.name = child_name
                    child_perm.pid = pid
                    updated_count += 1
            else:
                child_id = auto_id
                auto_id += 1
                session.add(SystemPermission(id=child_id, pid=pid, name=child_name, key=child_key))
                key_to_id[child_key] = child_id
                created_count += 1

    await session.commit()
    print(f"  ✅ 同步完成: 新增 {created_count} 个, 更新 {updated_count} 个, 删除 {removed_count} 个")


@app.command("sync")
def sync(
    reset: bool = typer.Option(False, "--reset", help="清空已有权限后重新同步"),
):
    """从代码定义同步权限到数据库"""
    import asyncio

    async def run():
        async with session_maker() as session:
            await _sync_tree(session, reset=reset)

    print("开始同步权限...")
    asyncio.run(run())
