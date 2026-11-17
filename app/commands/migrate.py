"""
数据库迁移命令 — 基于 Alembic

用法:
    uv run artisan.py migrate init              # 初始化 Alembic（首次使用）
    uv run artisan.py migrate revision -m "xxx"  # 创建空迁移
    uv run artisan.py migrate revision --autogenerate -m "xxx"  # 自动生成迁移
    uv run artisan.py migrate upgrade            # 执行迁移
    uv run artisan.py migrate downgrade          # 回退迁移
    uv run artisan.py migrate current            # 查看当前版本
    uv run artisan.py migrate history            # 查看迁移历史
    uv run artisan.py migrate show <revision>    # 查看迁移详情
    uv run artisan.py migrate create-tables      # 直接建表（开发用，跳过迁移）
"""

import os
import typer
from pathlib import Path

app = typer.Typer(help="数据库迁移管理")


ALEMBIC_DIR = Path("alembic")
ALEMBIC_INI = Path("alembic.ini")


def _ensure_alembic():
    """确保 Alembic 已初始化"""
    if not ALEMBIC_INI.exists():
        typer.echo("❌ Alembic 未初始化，请先执行:")
        typer.echo("  uv run artisan.py migrate init")
        raise typer.Exit(1)
    if not ALEMBIC_DIR.exists():
        typer.echo("❌ alembic/ 目录不存在，请先执行:")
        typer.echo("  uv run artisan.py migrate init")
        raise typer.Exit(1)


def _get_alembic_config():
    """获取 Alembic 配置"""
    from alembic.config import Config
    return Config(ALEMBIC_INI)


@app.command("init")
def init():
    """初始化 Alembic 迁移环境"""
    if ALEMBIC_INI.exists() and ALEMBIC_DIR.exists():
        typer.echo("⚠️  Alembic 已初始化，如需重新初始化请先删除 alembic.ini 和 alembic/ 目录")
        raise typer.Exit(1)

    typer.echo("初始化 Alembic 迁移环境...")

    # 生成 alembic.ini
    ini_content = """\
# Alembic 配置文件

[alembic]
script_location = alembic
sqlalchemy.url = placeholder

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
"""
    with open(ALEMBIC_INI, "w") as f:
        f.write(ini_content)

    # 创建 alembic 目录
    ALEMBIC_DIR.mkdir(exist_ok=True)
    (ALEMBIC_DIR / "versions").mkdir(exist_ok=True)

    # 生成 env.py — 同步引擎，从 app.core.config 动态读取数据库 URL
    env_content = '''\
"""Alembic 环境配置 — 从 app.core.config 读取数据库连接"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from sqlmodel import SQLModel

# 导入所有模型以便自动生成迁移时能检测到
from app.models import (
    SystemAdmin, SystemPermission, SystemAdminPermission, SystemAdminLogin,
    Article, Example, Setting, User, Region,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url():
    """从 app.core.config 获取数据库 URL"""
    from app.core.config import config as app_config
    return str(app_config.db_connect)


def run_migrations_offline() -> None:
    """离线模式 — 生成 SQL 脚本"""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式 — 连接数据库执行迁移"""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''
    with open(ALEMBIC_DIR / "env.py", "w") as f:
        f.write(env_content)

    # 生成 script.py.mako
    mako_content = '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
'''
    with open(ALEMBIC_DIR / "script.py.mako", "w") as f:
        f.write(mako_content)

    typer.echo("✅ Alembic 初始化完成")
    typer.echo("  生成文件:")
    typer.echo(f"    {ALEMBIC_INI}")
    typer.echo(f"    {ALEMBIC_DIR}/env.py")
    typer.echo(f"    {ALEMBIC_DIR}/script.py.mako")
    typer.echo(f"    {ALEMBIC_DIR}/versions/")
    typer.echo("")
    typer.echo("  下一步:")
    typer.echo('    uv run artisan.py migrate revision --autogenerate -m "init"')


@app.command("revision")
def revision(
    message: str = typer.Option(..., "-m", "--message", help="迁移描述"),
    autogenerate: bool = typer.Option(False, "--autogenerate", help="自动检测模型变更"),
):
    """创建迁移脚本"""
    _ensure_alembic()
    from alembic.command import revision as alembic_revision

    alembic_cfg = _get_alembic_config()
    alembic_revision(alembic_cfg, message=message, autogenerate=autogenerate)
    typer.echo("✅ 迁移脚本已创建")


@app.command("upgrade")
def upgrade(revision: str = typer.Argument("head", help="目标版本（默认最新）")):
    """执行迁移"""
    _ensure_alembic()
    from alembic.command import upgrade as alembic_upgrade

    alembic_cfg = _get_alembic_config()
    alembic_upgrade(alembic_cfg, revision)
    typer.echo(f"✅ 数据库已升级到 {revision}")
    _sync_plugins()


@app.command("stamp")
def stamp(revision: str = typer.Argument(help="标记当前库所处的版本（如 0005），不执行任何 SQL")):
    """把现有库标记到指定版本

    适用：建库时走 `db init`（create_all 全量建表，无 alembic 版本记录）的库
    引入新迁移时，先 stamp 到与当前表结构一致的版本，再 `migrate upgrade` 增量演进。
    """
    _ensure_alembic()
    from alembic.command import stamp as alembic_stamp

    alembic_cfg = _get_alembic_config()
    alembic_stamp(alembic_cfg, revision)
    typer.echo(f"✅ 已标记当前库为 {revision}（未执行 SQL），后续用 migrate upgrade 增量迁移")


@app.command("downgrade")
def downgrade(revision: str = typer.Argument("-1", help="目标版本（默认回退一个版本）")):
    """回退迁移"""
    _ensure_alembic()
    from alembic.command import downgrade as alembic_downgrade

    alembic_cfg = _get_alembic_config()
    alembic_downgrade(alembic_cfg, revision)
    typer.echo(f"✅ 数据库已回退到 {revision}")


@app.command("current")
def current():
    """查看当前数据库版本"""
    _ensure_alembic()
    from alembic.command import current as alembic_current

    alembic_cfg = _get_alembic_config()
    alembic_current(alembic_cfg)


@app.command("history")
def history(verbose: bool = typer.Option(False, "-v", "--verbose", help="显示详细信息")):
    """查看迁移历史"""
    _ensure_alembic()
    from alembic.command import history as alembic_history

    alembic_cfg = _get_alembic_config()
    alembic_history(alembic_cfg, verbose=verbose)


@app.command("show")
def show(revision: str = typer.Argument(..., help="迁移版本号")):
    """查看指定版本的迁移脚本"""
    _ensure_alembic()
    from alembic.command import show as alembic_show

    alembic_cfg = _get_alembic_config()
    alembic_show(alembic_cfg, revision)


@app.command("create-tables")
def create_tables():
    """直接根据 SQLModel 定义创建所有表（开发用，跳过迁移）"""
    import asyncio

    from sqlmodel import SQLModel
    from app.core.database import engine

    # 确保所有模型已导入（全部模型集中在 app.models，导入即注册 metadata）
    import app.models  # noqa: F401

    async def _create_all():
        # engine 是 AsyncEngine，需经 run_sync 调用同步 DDL
        async with engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    typer.echo(f"数据库: {engine.url}")
    asyncio.run(_create_all())
    typer.echo("✅ 所有表已创建")
    _sync_plugins()


def _sync_plugins():
    """建表/迁移后补齐 plugins 表（B05：否则全新安装插件计数为空）。

    幂等；失败只告警不中断迁移（表结构已就位，插件可稍后在后台点「同步插件」补）。
    """
    import asyncio

    async def _run():
        from app.core.database import session_maker
        from app.services.plugin_sync import sync_plugins

        async with session_maker() as session:
            return await sync_plugins(session)

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — 迁移不因插件同步失败而回滚
        typer.echo(f"⚠️  插件表同步失败（可稍后在后台「同步插件」或重跑 db init）：{exc}")
        return
    typer.echo(f"✅ {result['message']}")
