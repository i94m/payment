"""支付通道加「设为默认」标记 channels.is_default

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-25 15:05:00.000000

默认通道只作用于展示：商户端「对接信息」与后台「API密钥」弹窗展示这条通道的名称，
方便对接方一眼看到平台当前主用的通道。**不参与分流**（分流仍由编码绑定 / 金额路由决定）。

全平台只允许一条：设置走 `PUT /admin-api/pay/channels/{id}/default`，同事务把其它通道清 0；
停用或删除默认通道时一并清标记，避免商户端展示一条已停用的通道。

MySQL 8 走 `ALGORITHM=INSTANT`（只改数据字典，不锁表、不重建）；不支持时回落普通
ADD COLUMN。通道表是运营手工维护的小表，取默认通道时按 `is_default` 过滤，不加索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError


revision: str = '0021'
down_revision: Union[str, None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(table: str, column: sa.Column) -> None:
    """加列：MySQL 优先 INSTANT，失败回落普通 ADD COLUMN（与 0017/0018 同口径）。"""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        from sqlalchemy.schema import CreateColumn

        ddl = str(CreateColumn(column).compile(dialect=bind.dialect))
        try:
            bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {ddl}, ALGORITHM=INSTANT"))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0021] INSTANT 加列不可用（{e.orig}），回落普通 ADD COLUMN：{table}.{column.name}")
    op.add_column(table, column)


def upgrade() -> None:
    # server_default 保证已有行直接回填 0（非默认通道），上线后由运营手动指定
    _add_column(
        'channels',
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('channels', 'is_default')
