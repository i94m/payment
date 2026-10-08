"""收款页短码 pay_tokens

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pay_tokens',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('token', sa.String(length=12), nullable=False),
    sa.Column('merchant_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('agent_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token', name='uq_pay_tokens_token'),
    sa.UniqueConstraint('merchant_id', 'agent_id', name='uq_pay_tokens_merchant_agent'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_unicode_ci',
    mysql_engine='InnoDB'
    )


def downgrade() -> None:
    op.drop_table('pay_tokens')
