"""下游通道编码：channel_codes / channel_code_items + orders.merchants 加列

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-20 09:00:00.000000

- `channel_codes`：编码主档（全局唯一命名空间；mode 是编码级策略）
- `channel_code_items`：编码 → 通道绑定（权重/本编码内临时开关/轮询游标）
- `orders.channel_code` / `orders.channel_code_id` + 索引 (channel_code_id, created_at)
- `merchants.channel_code_required`（下游下单是否必传编码；存量商户默认 0）

**量级与锁表评估（orders 是全站最大的表）**

三类 DDL 的代价完全不同，本迁移按代价从低到高分别处理：

1. 两张新表：纯 CREATE TABLE，不碰存量数据。
2. `orders` 加两列：MySQL 8.0.12+ 走 `ALGORITHM=INSTANT`（只改数据字典，毫秒级，
   不重建表、不持写锁）；5.7 与 8.0.12 以下不支持 INSTANT，退化为 COPY（重建整表，
   持写锁）。`_add_column()` 先试 INSTANT，失败才回落普通 ADD COLUMN 并打日志。
3. `orders` 加二级索引：`ALGORITHM=INPLACE LOCK=NONE`（在线建索引，DML 不阻塞，
   只是 IO/CPU 开销）；老版本/特殊引擎回落到普通 CREATE INDEX。

实测（本机 docker，`orders` 造 100 万行 = 144MB 数据 + 198MB 索引）：

| DDL | MySQL 8.0 | MySQL 5.7.44（QEMU 模拟，偏保守） |
|---|---|---|
| 加 2 列 | INSTANT 0.08~0.11s | 不支持 INSTANT（error 1800）→ COPY **55.8s**，全程持写锁 |
| 建 `(channel_code_id, created_at)` 索引 | INPLACE LOCK=NONE **0.88s** | INPLACE LOCK=NONE **10.5s**（不阻塞读写） |

存量库最坏情况（MySQL 5.7 + 大表）第 2 步是整表 COPY，这是本迁移唯一的长时间操作。
两步上线路径（已实测可重入）：低峰期先手工跑一次只加列的 DDL（列已存在时本迁移会跳过，
见 `_column_exists`），随后正常发布只落后面的建索引（在线）。具体行数与耗时结论见 PR 描述。

`orders.channel_code` 可空、`channel_code_id` 默认 0：历史订单不写编码，语义是「未指定编码」，
与「编码被删除」区分开（删除编码时只解绑，不删历史留痕）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError


revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(bind, table: str, column: str) -> bool:
    """MySQL 走 information_schema；其它方言（sqlite 单机/测试）走 PRAGMA。

    两步上线时列可能已被提前加好，这一步保证迁移可重入。
    """
    if bind.dialect.name == "mysql":
        row = bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
        return row is not None
    rows = bind.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


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
    """加列：MySQL 优先 INSTANT，失败回落普通 ADD COLUMN（见模块 docstring 的评估）。"""
    bind = op.get_bind()
    if _column_exists(bind, table, column.name):
        print(f"[0014] {table}.{column.name} 已存在，跳过加列")
        return
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
        sa.Column('channel_code_required', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
    # 存量商户不强制（默认 0 即语义本身，无需回填）：老下游零改造
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
