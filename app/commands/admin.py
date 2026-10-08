"""
管理员管理命令

用法:
    uv run artisan.py admin create
    uv run artisan.py admin list
    uv run artisan.py admin passwd
    uv run artisan.py admin forbid
    uv run artisan.py admin active
"""

import asyncio

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func
from sqlmodel import select

from app.core.database import session_maker
from app.core.security import hash_password
from app.models import SystemAdmin, SystemPermission, SystemAdminPermission

app = typer.Typer()
console = Console()


@app.command()
def create():
    """创建管理员"""
    name = typer.prompt("请输入姓名")
    username = typer.prompt("请输入用户名")
    password = typer.prompt("请输入密码", hide_input=True)
    is_super_admin = typer.confirm("是否为超级管理员", default=False)

    async def run():
        async with session_maker() as session:
            user = SystemAdmin(
                username=username,
                password=hash_password(password),
                name=name,
                is_super_admin=is_super_admin,
            )
            session.add(user)
            await session.commit()
            typer.secho(f"{username} -> {password} 创建成功", fg=typer.colors.GREEN)

    asyncio.run(run())


@app.command("list")
def list_users():
    """管理员列表"""
    typer.secho("管理员列表", fg=typer.colors.GREEN)
    table = Table("ID", "用户名", "姓名", "权限", "禁用", "超管", "活跃时间", "活跃IP")

    async def run():
        async with session_maker() as session:
            stmt = (
                select(SystemAdmin.id, SystemAdmin.username, SystemAdmin.name,
                       SystemAdmin.is_forbid, SystemAdmin.is_super_admin,
                       SystemAdmin.last_active_at, SystemAdmin.last_active_ip,
                       func.group_concat(SystemPermission.key))
                .outerjoin(SystemAdminPermission, SystemAdminPermission.admin_id == SystemAdmin.id)
                .outerjoin(SystemPermission, SystemPermission.id == SystemAdminPermission.permission_id)
                .group_by(SystemAdmin.id)
            )
            rows = (await session.exec(stmt)).all()
            for row in rows:
                table.add_row(
                    str(row[0]), row[1], row[2] or "", row[7] or "",
                    "是" if row[3] else "", "是" if row[4] else "",
                    row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else "", row[6] or "",
                )
            console.print(table)

    asyncio.run(run())


@app.command()
def passwd():
    """重置密码"""
    username = typer.prompt("请输入用户名")
    password = typer.prompt("请输入新密码", hide_input=True)

    async def run():
        async with session_maker() as session:
            user = (await session.exec(
                select(SystemAdmin).where(SystemAdmin.username == username)
            )).first()
            if not user:
                typer.secho("账号不存在", fg=typer.colors.RED, bold=True)
                raise typer.Exit(code=1)
            user.password = hash_password(password)
            session.add(user)
            await session.commit()
            typer.secho(f"密码重置成功: {username} -> {password}", fg=typer.colors.GREEN, bold=True)

    asyncio.run(run())


@app.command()
def forbid():
    """禁用账户"""
    username = typer.prompt("请输入用户名")

    async def run():
        async with session_maker() as session:
            user = (await session.exec(
                select(SystemAdmin).where(SystemAdmin.username == username)
            )).first()
            if not user:
                typer.secho("账号不存在", fg=typer.colors.RED, bold=True)
                raise typer.Exit(code=1)
            user.is_forbid = True
            await session.commit()
            typer.secho(f"账户 {username} 已禁用", fg=typer.colors.GREEN, bold=True)

    asyncio.run(run())


@app.command()
def active():
    """解禁账户"""
    username = typer.prompt("请输入用户名")

    async def run():
        async with session_maker() as session:
            user = (await session.exec(
                select(SystemAdmin).where(SystemAdmin.username == username)
            )).first()
            if not user:
                typer.secho("账号不存在", fg=typer.colors.RED, bold=True)
                raise typer.Exit(code=1)
            user.is_forbid = False
            await session.commit()
            typer.secho(f"账户 {username} 已解禁", fg=typer.colors.GREEN, bold=True)

    asyncio.run(run())


if __name__ == "__main__":
    app()
