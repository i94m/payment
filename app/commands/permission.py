"""
权限同步命令 — 从代码定义同步权限到数据库

用法:
    uv run artisan.py permission sync
    uv run artisan.py permission sync --reset

权限树定义在 PERMISSION_TREE 列表中，修改后运行 sync 即可同步到数据库；
超管也可以在后台「系统 → 账号管理 → 同步权限」里先预览再执行（同一份对账逻辑，
见 app/services/permission.py）。
"""

import typer
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import session_maker
from app.models import SystemPermission
from app.services.permission import apply_sync

app = typer.Typer(help="管理权限")


async def _sync_tree(session: AsyncSession, reset: bool = False) -> dict:
    """同步权限树到数据库（异步），返回 {created, updated, removed}。"""
    if reset:
        for perm in (await session.exec(select(SystemPermission))).all():
            await session.delete(perm)
        await session.commit()
        print("  已清空权限表")

    result = await apply_sync(session)
    print(
        f"  ✅ 同步完成: 新增 {result['created']} 个, 更新 {result['updated']} 个, "
        f"删除 {result['removed']} 个"
    )
    return result


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
