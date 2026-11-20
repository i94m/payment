"""下游通道编码：channel_codes / channel_code_items + orders.merchants 加列

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-20 09:00:00.000000

- `channel_codes`：编码主档（全局唯一命名空间；mode 是编码级策略）
- `channel_code_items`：编码 → 通道绑定（权重/本编码内临时开关/轮询游标）
- `orders.channel_code` / `orders.channel_code_id` + 索引 (channel_code_id, created_at)
- `merchants.channel_code_required`（下游下单必须携带编码；默认 1，商户级开关只做个别豁免）

`orders` 加列先试 `ALGORITHM=INSTANT`（MySQL 8.0.12+ 只改数据字典），不支持时回落
普通 ADD COLUMN；建索引用 `ALGORITHM=INPLACE LOCK=NONE`，不阻塞读写。

`orders.channel_code` 可空、`channel_code_id` 默认 0：未指定编码的订单（平台收银台、
余额充值等内部入口）不写编码，与「编码被删除」区分开（删除编码时只解绑，不删历史留痕）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(bind, table: str, index: str) -> bool:
    if bind.dialect.name == "mysql":
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"
            ),
            {"t": table, "i": index},
        ).first()
        return row is not None
    row = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :i"), {"i": index}
    ).first()
    return row is not None


def _add_column(table: str, column: sa.Column) -> None:
    """加列：MySQL 优先 INSTANT，失败回落普通 ADD COLUMN。"""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        from sqlalchemy.schema import CreateColumn

        ddl = str(CreateColumn(column).compile(dialect=bind.dialect))
        try:
            bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {ddl}, ALGORITHM=INSTANT"))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0014] INSTANT 加列不可用（{e.orig}），回落普通 ADD COLUMN：{table}.{column.name}")
    op.add_column(table, column)


def _create_index(table: str, name: str, columns: list[str]) -> None:
    """建索引：MySQL 走 INPLACE + LOCK=NONE（在线，不阻塞读写），失败回落普通 CREATE INDEX。"""
    bind = op.get_bind()
    if _index_exists(bind, table, name):
        print(f"[0014] 索引 {name} 已存在，跳过")
        return
    if bind.dialect.name == "mysql":
        cols = ", ".join(f"`{c}`" for c in columns)
        try:
            # 注意：ALGORITHM 与 LOCK 之间不能加逗号（MySQL 会报 1064 语法错误）
            bind.execute(sa.text(
                f"CREATE INDEX `{name}` ON `{table}` ({cols}) ALGORITHM=INPLACE LOCK=NONE"
            ))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0014] 在线建索引不可用（{e.orig}），回落普通 CREATE INDEX：{name}")
    op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    op.create_table(
        'channel_codes',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  primary_key=True, autoincrement=True),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=64), server_default=sa.text("''"), nullable=False),
        sa.Column('remark', sa.String(length=255), nullable=True),
        sa.Column('mode', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.Column('status', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('code', name='uq_channel_codes_code'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_table(
        'channel_code_items',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  primary_key=True, autoincrement=True),
        sa.Column('code_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('channel_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('weight', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('status', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
        sa.Column('sort', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_used_at', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('code_id', 'channel_id', name='uq_channel_code_items'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_unicode_ci',
    )
    _create_index('channel_code_items', 'ix_channel_code_items_code_status', ['code_id', 'status'])

    _add_column(
        'merchants',
        sa.Column('channel_code_required', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    )
    _add_column('orders', sa.Column('channel_code', sa.String(length=32), nullable=True))
    _add_column(
        'orders',
        sa.Column('channel_code_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
                  server_default=sa.text('0'), nullable=False),
    )
    _create_index('orders', 'ix_orders_channel_code_created', ['channel_code_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_orders_channel_code_created', table_name='orders')
    op.drop_column('orders', 'channel_code_id')
    op.drop_column('orders', 'channel_code')
    op.drop_column('merchants', 'channel_code_required')
    op.drop_index('ix_channel_code_items_code_status', table_name='channel_code_items')
    op.drop_table('channel_code_items')
    op.drop_table('channel_codes')
