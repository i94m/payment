"""通道编码下线轮询策略：存量 mode=1 改为随机

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-25 00:50:00.000000

后台「通道编码」只剩随机 / 权重两种策略，轮询不再提供、保存时直接拒绝。
库里可能已有 mode=1 的编码（该策略从 0014 起就存在），这里统一改成 0（随机）：
运行时代码对 mode=1 也是按随机分流，迁移只是把数据库里的值与实际行为对齐。

不改表结构、不加列；只动数据。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text('UPDATE channel_codes SET mode = 0 WHERE mode = 1'))


def downgrade() -> None:
    # 无法反推哪些编码原来是轮询（改过之后与手工选的随机无法区分），保持随机。
    pass
