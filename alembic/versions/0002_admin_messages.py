"""后台通知中心 admin_messages

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401


revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('admin_messages',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('content', sa.String(length=500), server_default=sa.text("''"), nullable=False),
    sa.Column('biz_type', sa.String(length=32), server_default=sa.text("'alert'"), nullable=False),
    sa.Column('read_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_unicode_ci',
    mysql_engine='InnoDB'
    )
    with op.batch_alter_table('admin_messages', schema=None) as batch_op:
        batch_op.create_index('ix_admin_messages_read', ['read_at', 'id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('admin_messages', schema=None) as batch_op:
        batch_op.drop_index('ix_admin_messages_read')
    op.drop_table('admin_messages')
