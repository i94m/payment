"""
数据库初始化命令

用法:
    uv run artisan.py db init
"""

import typer
from datetime import datetime
from sqlalchemy import func
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import config
from app.core.database import engine, session_maker
from app.core.security import hash_password
from app.models import PaymentType, Setting, SystemAdmin

app = typer.Typer(help="数据库初始化命令")

# 内置支付方式种子
PAY_TYPE_SEEDS = [
    {"code": "alipay", "name": "支付宝", "sort": 1, "device": 0},
    {"code": "wxpay", "name": "微信支付", "sort": 2, "device": 0},
    {"code": "qqpay", "name": "QQ钱包", "sort": 3, "device": 0},
    {"code": "bank", "name": "云闪付", "sort": 4, "device": 0},
]


@app.command()
def init():
    """数据初始化 — 自动建表、填充初始数据、同步权限"""
    import asyncio
    asyncio.run(_init_async())


@app.command()
def sync_columns():
    """存量库结构同步 — 补缺失列、清理已下线功能的遗留表/列。

    create_all 只建表不加列；已有部署升级代码后运行一次即可（幂等）。
    """
    import asyncio
    asyncio.run(_sync_columns_async())


async def _sync_columns_async():
    typer.secho("[结构同步] 开始对比 ORM 与数据库结构...", fg=typer.colors.CYAN)
    counter = {"added": 0}

    # 已下线功能的遗留结构：商户用户组（2026-09 删除）、保证金（2026-10 删除）
    _LEGACY_TABLES = {"merchant_groups"}
    _LEGACY_COLUMNS = {
        "merchants": {"group_id", "group_endtime", "deposit"},
        "merchant_records": {"deposit_before", "deposit_after"},
    }

    async with engine.begin() as conn:
        def _do_sync(sync_conn):
            from sqlalchemy import inspect as sa_inspect

            insp = sa_inspect(sync_conn)
            preparer = sync_conn.dialect.identifier_preparer
            # 列 DDL 须用方言 DDL compiler 生成（Column.compile 在列未挂表时只给列名）
            ddl_compiler = sync_conn.dialect.ddl_compiler(sync_conn.dialect, None)
            # 1. 补新表
            SQLModel.metadata.create_all(bind=sync_conn)
            # 2. 补缺失列
            for table in SQLModel.metadata.sorted_tables:
                existing = {c["name"] for c in insp.get_columns(table.name)}
                if not existing:
                    continue
                for column in table.columns:
                    if column.name in existing:
                        continue
                    col_spec = ddl_compiler.get_column_specification(column)
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE {preparer.quote(table.name)} ADD COLUMN {col_spec}"
                    )
                    typer.secho(f"  + {table.name}.{column.name}", fg=typer.colors.YELLOW)
                    counter["added"] += 1
            # 3. 清理遗留表
            for tname in _LEGACY_TABLES:
                if insp.has_table(tname):
                    sync_conn.exec_driver_sql(f"DROP TABLE {preparer.quote(tname)}")
                    typer.secho(f"  - 遗留表 {tname} 已删除", fg=typer.colors.YELLOW)
            # 4. 清理遗留列
            for tname, colnames in _LEGACY_COLUMNS.items():
                existing = {c["name"] for c in insp.get_columns(tname)}
                for cname in sorted(colnames & existing):
                    sync_conn.exec_driver_sql(
                        f"ALTER TABLE {preparer.quote(tname)} DROP COLUMN {preparer.quote(cname)}"
                    )
                    typer.secho(f"  - 遗留列 {tname}.{cname} 已删除", fg=typer.colors.YELLOW)

        await conn.run_sync(_do_sync)
    if counter["added"]:
        typer.secho(f"[结构同步] 完成，新增 {counter['added']} 列", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho("[结构同步] 结构已一致，无需变更", fg=typer.colors.GREEN)


async def _init_async():
    # 1. 建表
    typer.secho("[建表] 开始...", fg=typer.colors.CYAN)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    typer.secho("[建表] 完成", fg=typer.colors.GREEN)

    # 2. 填充初始数据
    async with session_maker() as session:
        await _adminer(session)
        await _seed_pay_types(session)
        await _seed_settings(session)

    # 3. 同步插件表（全新安装插件计数不再为 0，新增通道下拉可用）
    typer.secho("[插件] 开始同步...", fg=typer.colors.CYAN)
    async with session_maker() as session:
        result = await _sync_plugins(session)
    typer.secho(f"[插件] {result['message']}", fg=typer.colors.GREEN)

    # 4. 同步权限
    from app.commands.permission import _sync_tree
    typer.secho("[权限] 开始同步...", fg=typer.colors.CYAN)
    async with session_maker() as session:
        await _sync_tree(session)
    typer.secho("[权限] 完成", fg=typer.colors.GREEN)

    typer.secho("\n数据初始化完成", fg=typer.colors.GREEN, bold=True)


async def _sync_plugins(session: AsyncSession):
    """把插件注册表落库到 plugins 表（与后台「同步插件」同一实现，幂等）。"""
    from app.services.plugin_sync import sync_plugins

    return await sync_plugins(session)


async def _adminer(session: AsyncSession):
    """初始化超管账号（账号固定 superadmin / 123456，不再读取环境变量）"""
    username, password = "superadmin", "123456"

    user = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == username)
    )).first()
    if not user:
        row = SystemAdmin(
            name="超级管理员",
            username=username,
            password=hash_password(password),
            is_super_admin=True,
        )
        session.add(row)
        await session.commit()
        typer.secho(f"[超管账号] 创建完成: {username} / {password}", fg=typer.colors.GREEN, bold=True)
    else:
        typer.secho(f"[超管账号] {username} 已存在，跳过", fg=typer.colors.GREEN)


async def _seed_pay_types(session: AsyncSession):
    """内置支付方式"""
    for seed in PAY_TYPE_SEEDS:
        existing = (await session.exec(
            select(PaymentType).where(PaymentType.code == seed["code"])
        )).first()
        if not existing:
            session.add(PaymentType(**seed, status=1))
    await session.commit()
    typer.secho(f"[支付方式] 种子完成（{len(PAY_TYPE_SEEDS)} 项）", fg=typer.colors.GREEN)


async def _seed_settings(session: AsyncSession):
    """运行期设置默认值（全部注册表 key 逐项播种，只写缺省项）"""
    from app.constants.settings import SETTINGS_REGISTRY
    from app.core.settings import default_map

    existing = {row.key for row in (await session.exec(select(Setting))).all()}
    defaults = default_map()
    now = datetime.now()
    count = 0
    for key in SETTINGS_REGISTRY:
        if key not in existing:
            session.add(Setting(key=key, value=defaults[key], updated_at=now))
            count += 1
    await session.commit()
    typer.secho(f"[运行期设置] 写入默认值 {count} 项", fg=typer.colors.GREEN)


if __name__ == "__main__":
    app()
