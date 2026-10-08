from collections.abc import AsyncGenerator
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from .config import config


def _local_tz_offset() -> str:
    """应用本地时区偏移（+HH:MM）。用于 MySQL 会话时区对齐。"""
    off = datetime.now().astimezone().utcoffset()
    total_min = int((off.total_seconds() // 60)) if off else 0
    sign = "+" if total_min >= 0 else "-"
    h, m = divmod(abs(total_min), 60)
    return f"{sign}{h:02d}:{m:02d}"


if config.DB_SCHEME == "sqlite":
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{config.DB_DATABASE}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    # 服务器默认时间戳（CURRENT_TIMESTAMP）按 MySQL 会话时区求值，
    # 与应用侧 datetime.now()（本地时间）对齐，避免两种基准混写。
    engine = create_async_engine(
        str(config.db_connect),
        echo=False,
        pool_size=config.DB_POOL_SIZE,
        max_overflow=config.DB_MAX_OVERFLOW,
        pool_recycle=config.DB_POOL_RECYCLE,
        pool_pre_ping=True,
        connect_args={"init_command": f"SET time_zone='{_local_tz_offset()}'"},
    )

session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with session_maker() as session:
        yield session
