"""通道编码成员去掉「是否启用」开关：勾选即启用

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-20 10:30:00.000000

编码内的成员开关与「绑定」重复：要摘掉某条通道，直接取消勾选即可（删绑定行），
保留一个永远是 1 的开关只会让运营误以为有两种停用方式。

- 先删 `ix_channel_code_items_code_status`（否则 MySQL 只把 status 从复合索引里摘掉，
  留下一个语义不明的 (code_id) 索引）
- 再删 `channel_code_items.status`
- `uq_channel_code_items (code_id, channel_id)` 继续承担按编码查成员的检索
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = 'ix_channel_code_items_code_status'


def upgrade() -> None:
    op.drop_index(INDEX_NAME, table_name='channel_code_items')
    op.drop_column('channel_code_items', 'status')


def downgrade() -> None:
    op.add_column(
        'channel_code_items',
        sa.Column('status', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    )
    op.create_index(INDEX_NAME, 'channel_code_items', ['code_id', 'status'], unique=False)
