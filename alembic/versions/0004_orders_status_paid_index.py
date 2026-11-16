"""orders 增加全站「已支付 + 实付时间」复合索引

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17 00:00:00.000000

背景：admin 看板（首页总览/今日收入/近 N 日趋势）全部是
`WHERE status = 1 AND paid_at >= ? [AND paid_at < ?]` 的全站口径聚合。
已有索引里只有 `(merchant_id, paid_at)` 带 paid_at，前导列是 merchant_id，
全站查询吃不到；`(merchant_id, status, created_at)` / `(status, created_at)`
同理（created_at 不是 paid_at）。因此每次看板访问都退化为全表/全索引扫描。

本迁移补 `(status, paid_at)`：等值 status 定位后再按 paid_at 做范围扫描，
看板与趋势图从「扫全表」变为「扫近 N 天已支付行」。

SQLite（测试/单机）与 MySQL 8.0/5.7 均支持该复合索引，无数据改写。
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_orders_status_paid', 'orders', ['status', 'paid_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_orders_status_paid', table_name='orders')
