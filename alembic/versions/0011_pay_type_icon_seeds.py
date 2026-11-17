"""内置支付方式补齐图标（微信/支付宝/QQ/网银/京东/PayPal）

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18 18:00:00.000000

支付方式支持图标选择后，种子扩展为 6 项内置方式并各带一个内置图标
（图标文件位于 static/icons/pay，服务端挂载 /icons）。本迁移：
- 补齐缺失的内置方式（已存在的不改名、不改排序，避免覆盖后台配置）；
- 存量记录 icon 为空时回填默认图标。
可重复执行。
"""
from typing import Sequence, Union

from alembic import op


revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, name, sort, icon)，与 app/commands/db.py 的 PAY_TYPE_SEEDS 保持一致
_SEEDS = (
    ("wxpay", "微信支付", 2, "/icons/pay/wxpay.svg"),
    ("alipay", "支付宝", 1, "/icons/pay/alipay.svg"),
    ("qqpay", "QQ钱包", 3, "/icons/pay/qqpay.svg"),
    ("bank", "网银支付", 4, "/icons/pay/bank.svg"),
    ("jdpay", "京东支付", 5, "/icons/pay/jdpay.svg"),
    ("paypal", "PayPal", 6, "/icons/pay/paypal.svg"),
)


def upgrade() -> None:
    for code, name, sort, icon in _SEEDS:
        op.execute(
            f"""
            INSERT INTO payment_types (code, name, `device`, icon, status, sort)
            SELECT '{code}', '{name}', 0, '{icon}', 1, {sort} FROM DUAL
            WHERE NOT EXISTS (SELECT 1 FROM payment_types WHERE code = '{code}')
            """
        )
        op.execute(
            f"""
            UPDATE payment_types SET icon = '{icon}'
            WHERE code = '{code}' AND (icon IS NULL OR icon = '')
            """
        )


def downgrade() -> None:
    # 图标列本身保留（其它支付方式可能已自行配置），只清掉本迁移回填的内置图标
    for _code, _name, _sort, icon in _SEEDS:
        op.execute(
            f"UPDATE payment_types SET icon = NULL WHERE code = '{_code}' AND icon = '{icon}'"
        )
