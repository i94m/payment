"""清理通道测试支付的历史配置项

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18 10:00:00.000000

通道测试改为「纯通道自测」：直接用通道凭证向上游下单，不建订单、不涉及商户，
因此 `test_pay_enable` / `test_pay_merchant_pid` 两个运行期配置项已从注册表移除。
删除残留行，避免设置表里留着无人读取的键。
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM settings WHERE `key` IN ('test_pay_enable', 'test_pay_merchant_pid')"
    )


def downgrade() -> None:
    # 回滚只恢复注册开关的默认值；PID 默认 0（自动挂单逻辑已下线）
    op.execute(
        """
        INSERT INTO settings (`key`, value)
        SELECT 'test_pay_enable', TRUE FROM DUAL
        WHERE NOT EXISTS (SELECT 1 FROM settings WHERE `key` = 'test_pay_enable')
        """
    )
    op.execute(
        """
        INSERT INTO settings (`key`, value)
        SELECT 'test_pay_merchant_pid', 0 FROM DUAL
        WHERE NOT EXISTS (SELECT 1 FROM settings WHERE `key` = 'test_pay_merchant_pid')
        """
    )
