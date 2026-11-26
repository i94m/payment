"""默认通道标记从 channels 移到 channel_codes

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-26 10:00:00.000000

默认通道其实应该是**通道编码**：商户端「对接信息」给接入方的是下游直接当
`channel_code` 传的那个编码，而不是上游通道名。0021 把标记加在了 `channels` 上，
这里把标记整体搬过来：

1. `channel_codes.is_default`（新增，server_default=0，已有行自动回填）
2. 存量数据搬移：若 `channels.is_default=1` 的那条通道绑定了编码，把它的第一个
   启用中编码标为默认（老库不会因为这次搬迁丢掉运营已经指定过的默认项）
3. `channels.is_default` 删除

`channel_codes` 是运营手工维护的小表，取默认编码时按 `is_default` 过滤，不加索引。
MySQL 8 走 `ALGORITHM=INSTANT`（只改数据字典，不锁表、不重建）；不支持的版本回落普通 DDL。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError, ProgrammingError


revision: str = '0022'
down_revision: Union[str, None] = '0021'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column(table: str, column: sa.Column) -> None:
    """加列：MySQL 优先 INSTANT，失败回落普通 ADD COLUMN（与 0017/0018/0021 同口径）。"""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        from sqlalchemy.schema import CreateColumn

        ddl = str(CreateColumn(column).compile(dialect=bind.dialect))
        try:
            bind.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {ddl}, ALGORITHM=INSTANT"))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0022] INSTANT 加列不可用（{e.orig}），回落普通 ADD COLUMN：{table}.{column.name}")
    op.add_column(table, column)


def _drop_column(table: str, name: str) -> None:
    """删列：MySQL 8 优先 INSTANT，失败回落普通 DROP COLUMN（MySQL 5.7 只支持 COPY）。"""
    bind = op.get_bind()
    if bind.dialect.name == "mysql":
        try:
            bind.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {name}, ALGORITHM=INSTANT"))
            return
        except (OperationalError, ProgrammingError) as e:
            print(f"[0022] INSTANT 删列不可用（{e.orig}），回落普通 DROP COLUMN：{table}.{name}")
    op.drop_column(table, name)


def _first_enabled_code_of_default_channel() -> int | None:
    """存量默认通道绑定的第一个启用编码 id（没有任何绑定/都停用则 None）。"""
    row = op.get_bind().execute(sa.text(
        "SELECT id FROM channels WHERE is_default = 1 ORDER BY id LIMIT 1"
    )).first()
    if not row:
        return None
    code = op.get_bind().execute(sa.text(
        "SELECT c.id FROM channel_codes c"
        " JOIN channel_code_items i ON i.code_id = c.id"
        " WHERE i.channel_id = :cid AND c.status = 1"
        " ORDER BY c.id LIMIT 1"
    ), {"cid": row[0]}).first()
    return code[0] if code else None


def _first_channel_of_default_code() -> int | None:
    """存量默认编码绑定的第一个通道 id（降级用；没有绑定则 None）。"""
    row = op.get_bind().execute(sa.text(
        "SELECT id FROM channel_codes WHERE is_default = 1 ORDER BY id LIMIT 1"
    )).first()
    if not row:
        return None
    ch = op.get_bind().execute(sa.text(
        "SELECT channel_id FROM channel_code_items WHERE code_id = :cid"
        " ORDER BY sort, id LIMIT 1"
    ), {"cid": row[0]}).first()
    return ch[0] if ch else None


def upgrade() -> None:
    _add_column(
        'channel_codes',
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
    # 数据搬移必须在删 channels.is_default 之前做
    code_id = _first_enabled_code_of_default_channel()
    if code_id:
        op.get_bind().execute(
            sa.text("UPDATE channel_codes SET is_default = 1 WHERE id = :id"), {"id": code_id}
        )
    _drop_column('channels', 'is_default')


def downgrade() -> None:
    _add_column(
        'channels',
        sa.Column('is_default', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
    channel_id = _first_channel_of_default_code()
    if channel_id:
        op.get_bind().execute(
            sa.text("UPDATE channels SET is_default = 1 WHERE id = :id"), {"id": channel_id}
        )
    _drop_column('channel_codes', 'is_default')
