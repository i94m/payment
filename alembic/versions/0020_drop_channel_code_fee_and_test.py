"""通道编码下线「费率」与「测试通道」两个字段

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-25 15:00:00.000000

需求方拍板：编码不再自带费率、也不再区分测试/正式用途。

- `channel_codes.fee_rate`：编码级商户费率整列删除，生效费率回到
  「商户专属费率 > 通道费率」两级（`effective_merchant_rate`）。
  **上线前务必导出对照**：`SELECT code, fee_rate FROM channel_codes WHERE fee_rate IS NOT NULL`，
  原来靠编码费率定价的编码会回落到通道费率，价格会变，不能静默发生。
- `channel_codes.is_test`：商户端「对接信息」不再展示业务编码，改展示
  `channels.is_default`（默认通道，迁移 0021），这个标记没有消费方了。

历史订单的 `orders.channel_code` 留痕不动。删列不可逆，downgrade 只能补回空列（值丢失）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0020'
down_revision: Union[str, None] = '0019'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('channel_codes', 'fee_rate')
    op.drop_column('channel_codes', 'is_test')


def downgrade() -> None:
    # 无法恢复被删掉的值：补回字段，全部按「不覆盖费率 / 正式编码」初始化。
    op.add_column('channel_codes', sa.Column('fee_rate', sa.Numeric(5, 2), nullable=True))
    op.add_column(
        'channel_codes',
        sa.Column('is_test', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
