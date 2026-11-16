"""后台敏感操作审计 system_audit_logs

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17 00:00:00.000000

新增审计表（只增不改）：操作人 / 动作 / 对象 / 前后值 / 时间 / 来源 IP。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('system_audit_logs',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
    sa.Column('actor_guard', sa.String(length=16), server_default=sa.text("'system'"), nullable=False),
    sa.Column('actor_id', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('actor_name', sa.String(length=64), server_default=sa.text("''"), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=32), server_default=sa.text("''"), nullable=False),
    sa.Column('target_id', sa.String(length=64), server_default=sa.text("''"), nullable=False),
    sa.Column('before', sa.JSON(), nullable=True),
    sa.Column('after', sa.JSON(), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('remark', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    mysql_charset='utf8mb4',
    mysql_collate='utf8mb4_unicode_ci',
    mysql_engine='InnoDB'
    )
    with op.batch_alter_table('system_audit_logs', schema=None) as batch_op:
        batch_op.create_index('ix_system_audit_logs_action', ['action', 'created_at'], unique=False)
        batch_op.create_index('ix_system_audit_logs_target', ['target_type', 'target_id'], unique=False)
        batch_op.create_index('ix_system_audit_logs_actor', ['actor_guard', 'actor_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('system_audit_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_system_audit_logs_actor')
        batch_op.drop_index('ix_system_audit_logs_target')
        batch_op.drop_index('ix_system_audit_logs_action')
    op.drop_table('system_audit_logs')
