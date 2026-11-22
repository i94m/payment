"""通道编码加「测试编码」标记 channel_codes.is_test

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-22 11:40:00.000000

商户端「对接信息」卡片只展示测试编码：原来按名称/备注含「测试」猜，现在改成显式标记。
标记挂在业务编码（`channel_codes`）而不是上游通道（`channels`）—— 卡片展示的就是编码，
标记要跟着被展示的那条走。默认 0（正式编码），不改变任何现有编码的行为。

MySQL 8 走 `ALGORITHM=INSTANT`（只改数据字典，不锁表、不重建）；不支持时回落普通
ADD COLUMN。先试不加索引：编码表是运营手工维护的小表，取用时按 `(status, is_test, id)`
过滤，没必要为它多一个二级索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError


revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(table: str, column: sa.Column) -> None:
    """加列：MySQL 优先 INSTANT，失败回落普通 ADD COLUMN（与 0014 同口径）。"""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        from sqlalchemy.schema import CreateColumn

        ddl = str(CreateColumn(column).compile(dialect=bind.dialect))
        try:
            bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {ddl}, ALGORITHM=INSTANT"))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0016] INSTANT 加列不可用（{e.orig}），回落普通 ADD COLUMN：{table}.{column.name}")
    op.add_column(table, column)


def upgrade() -> None:
    # server_default 保证已有行直接回填 0（正式编码），不需要单独 UPDATE
    _add_column(
        'channel_codes',
        sa.Column('is_test', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('channel_codes', 'is_test')
