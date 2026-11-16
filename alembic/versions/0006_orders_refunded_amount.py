"""orders 退款配额累计列 refunded_amount

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17 00:00:00.000000

退款并发上限的可移植守卫依赖这一列：
`UPDATE orders SET refunded_amount = refunded_amount + :amt
  WHERE trade_no=? AND status=1 AND refunded_amount + :amt <= 可退上限`
影响行数判定，MySQL/SQLite 同语义（不依赖 FOR UPDATE）。

合并注意（集成顺序 #8 → #10 → #9 → #12 → #11）：本 revision 在资金分支上挂在 0003
（保证分支自身 `alembic heads` 单头）。合入时接在集成链末尾：
#8 的 `0004_orders_status_paid_index` → #10 的 `0005_system_audit_logs` → 本 revision，
全链始终单头。

已存在退款的订单按「成功 + 在途」退款单求和回填，避免升级后老单可退额度被放宽。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'refunded_amount', sa.Numeric(precision=12, scale=2),
            server_default=sa.text('0.00'), nullable=False,
        ))

    # 回填：已退 + 在途预占（与 services/refund.py 的配额口径一致）
    op.execute(
        "UPDATE orders SET refunded_amount = COALESCE(("
        "  SELECT SUM(refunds.amount) FROM refunds"
        "  WHERE refunds.trade_no = orders.trade_no AND refunds.status IN (0, 1)"
        "), 0)"
    )


def downgrade() -> None:
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_column('refunded_amount')
