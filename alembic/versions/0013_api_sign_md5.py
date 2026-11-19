"""开放 API 签名：MD5 默认 + RSA2 可选

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-19 15:10:00.000000

- `merchants.api_secret`：MD5 签名密钥（32 位小写 hex，Fernet 密文），创建商户即生成
- `merchants.rsa2_enable`：RSA2 是否对商户开放（默认 0，需商户面板开启 + 配置公钥）
- `orders.sign_type`：下单使用的签名算法（MD5/RSA2），通知与回跳按单签名

全新系统，无需数据回填：新库由 create_all 建表，老库 upgrade 后按默认值生效。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('merchants', sa.Column('api_secret', sa.Text(), nullable=True))
    op.add_column(
        'merchants',
        sa.Column('rsa2_enable', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'orders',
        sa.Column('sign_type', sa.String(length=8), server_default=sa.text("'MD5'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('orders', 'sign_type')
    op.drop_column('merchants', 'rsa2_enable')
    op.drop_column('merchants', 'api_secret')
