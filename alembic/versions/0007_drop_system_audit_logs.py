"""移除后台操作审计表 system_audit_logs

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17 09:30:00.000000

审计日志功能整体下线（只读审计页 + 前端菜单 + 后端 record_audit 调用一并删除），
对应的 `system_audit_logs` 表与 `system.audit` 权限不再保留：

- 表：直接删除（历史审计数据随之丢弃；如需留档请在执行本迁移前自行备份）
- 权限：由 `uv run python artisan.py permission sync` 按 PERMISSION_TREE 清理
  （key 已从代码中移除，sync 会删除该权限行及其角色关联）

`0005_system_audit_logs` 保持原样不改写历史；新建库会按 0005 建表再被本迁移删除。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('system_audit_logs')


def downgrade() -> None:
    """回滚：按 0005 的结构重建空表（历史数据不可恢复）。"""
    op.create_table(
        'system_audit_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
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
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_system_audit_logs_action', 'system_audit_logs', ['action', 'created_at'])
    op.create_index('ix_system_audit_logs_target', 'system_audit_logs', ['target_type', 'target_id'])
    op.create_index('ix_system_audit_logs_actor', 'system_audit_logs', ['actor_guard', 'actor_id'])
