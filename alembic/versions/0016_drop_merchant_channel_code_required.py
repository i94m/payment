"""去掉「下游通道编码必传」：通道编码改为可选参数

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-20 15:00:00.000000

编码必传属于平台侧契约，实际运营中用不上，且与「编码可选的默认路由」相比只会
让下游对接多一道配置门槛。去掉后：

- 开放 API 下单不再校验 `channel_code` 是否存在；携带时仍走编码内分流与参数校验。
- `merchants.channel_code_required` 整列删除（默认值 1，个别豁免随之失效）。
- 历史订单的 `orders.channel_code` / `channel_code_id` 留痕不动。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('merchants', 'channel_code_required')


def downgrade() -> None:
    op.add_column(
        'merchants',
        sa.Column('channel_code_required', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    )
