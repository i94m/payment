"""商户随机金额增加「仅整数金额」开关

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17 15:00:00.000000

`merchants.random_integer`：随机金额是否只允许整数。

- 关闭（默认，0）：区间内按分（两位小数）随机，如 1.00 ~ 5.00
- 开启（1）：区间内只随机整数，如 1 ~ 5

历史商户默认 0：既有配置多为整数区间，取整后仍落在区间内，行为不变。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'merchants',
        sa.Column('random_integer', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('merchants', 'random_integer')
