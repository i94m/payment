"""清理未使用的旧内置支付方式（只保留支付宝 / 微信支付）

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-17 16:00:00.000000

业务只对接支付宝（alipay）与微信支付（wxpay），种子已收敛为这两项。
存量库里 qqpay / bank 等旧内置方式若仍被通道或订单引用则保留（避免通道失效、
历史订单统计断档），仅删除完全未被引用的空记录；可重复执行。
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEEP = "('alipay', 'wxpay')"


def upgrade() -> None:
    op.execute(
        f"""
        DELETE FROM payment_types
        WHERE code NOT IN {_KEEP}
          AND id NOT IN (SELECT DISTINCT payment_type_id FROM channels)
          AND id NOT IN (SELECT DISTINCT payment_type_id FROM orders)
        """
    )


def downgrade() -> None:
    # 回滚只补回旧内置方式（不恢复已删除的关联，业务上不需要）
    op.execute(
        """
        INSERT INTO payment_types (code, name, `device`, status, sort)
        SELECT 'qqpay', 'QQ钱包', 0, 1, 3 FROM DUAL
        WHERE NOT EXISTS (SELECT 1 FROM payment_types WHERE code = 'qqpay')
        """
    )
    op.execute(
        """
        INSERT INTO payment_types (code, name, `device`, status, sort)
        SELECT 'bank', '云闪付', 0, 1, 4 FROM DUAL
        WHERE NOT EXISTS (SELECT 1 FROM payment_types WHERE code = 'bank')
        """
    )
