"""通道编码加「单笔金额区间」与「编码费率」

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-25 10:10:00.000000

- `min_amount` / `max_amount`：编码级单笔金额区间（两端含，0=不限，两边都 0 = 不限制）。
  超出范围在建单参数校验阶段直接拒绝，不进入通道选择 —— 编码是下游的产品边界。
- `fee_rate`：编码级商户费率（%），NULL=不覆盖，走「商户专属费率 > 通道费率」原链路。

三列都有默认值/NULL，存量编码行为保持不变（不限额、不覆盖费率），不需要单独回填。

MySQL 8 走 `ALGORITHM=INSTANT`（只改数据字典，不锁表、不重建）；不支持时回落普通
ADD COLUMN。编码表是运营手工维护的小表，不加索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError


revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(table: str, column: sa.Column) -> None:
    """加列：MySQL 优先 INSTANT，失败回落普通 ADD COLUMN（与 0014/0017 同口径）。"""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        from sqlalchemy.schema import CreateColumn

        ddl = str(CreateColumn(column).compile(dialect=bind.dialect))
        try:
            bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {ddl}, ALGORITHM=INSTANT"))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0018] INSTANT 加列不可用（{e.orig}），回落普通 ADD COLUMN：{table}.{column.name}")
    op.add_column(table, column)


def upgrade() -> None:
    _add_column(
        'channel_codes',
        sa.Column('min_amount', sa.Numeric(12, 2), server_default=sa.text('0.00'), nullable=False),
    )
    _add_column(
        'channel_codes',
        sa.Column('max_amount', sa.Numeric(12, 2), server_default=sa.text('0.00'), nullable=False),
    )
    # 空 = 不覆盖费率：必须可空，不能用 0 代替（0 是「免手续费」，语义完全不同）
    _add_column(
        'channel_codes',
        sa.Column('fee_rate', sa.Numeric(5, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('channel_codes', 'fee_rate')
    op.drop_column('channel_codes', 'max_amount')
    op.drop_column('channel_codes', 'min_amount')
