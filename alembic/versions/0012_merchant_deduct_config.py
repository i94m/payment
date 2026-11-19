"""扣量配置从全局 settings 下放到商户行

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-19 12:00:00.000000

`merchants` 新增三列（全部 NOT NULL + 默认关，不做「继承全局」语义）：

- `deduct_enable`   BOOL          默认 0，唯一开关
- `deduct_frequency` INT          默认 0，每 N 笔扣 1（0=不扣，仅作防御；停用请用开关）
- `deduct_minmoney` DECIMAL(12,2) 默认 0.00，起扣金额（单笔 order.money ≥ 该值才参与，0=不限）

同一 revision 内完成三件事，不留「加了列还没回填」的中间态：

1. 加三列；
2. 读旧全局 `deduct_enable` / `deduct_frequency` / `deduct_minmoney`，开关为真时把三个旧值
   一次性灌给**全部**商户（不筛「在用的」：升级前全局参数对所有商户生效，全量回填后逐笔判定
   条件与升级前完全一致，不会出现休眠商户回来进单时静默不扣）；开关为假则不动商户行，
   列默认值即「全关」，与现网等价。旧值缺失时取注册表历史默认 False / 0 / "0.00"。
3. 删除三条旧 settings 行 —— 注册表已移除这三个 key，页面不再展示，残留行是隐形雷
   （将来加回同名 key / 配置导入导出会让扣量在没人操作时复活）。值已灌进商户行且打印留痕。

注意：deploy.sh 是「起容器 → 等 healthy → 再 upgrade」，加列期间旧镜像 SELECT 不到列是硬错误，
本次一次加 3 列，建议低峰发布；所有加列 release 同理，非本次引入。
"""
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_KEYS = ('deduct_enable', 'deduct_frequency', 'deduct_minmoney')
# settings 表 JSON 列在旧库里可能存 bool / int / 字符串，缺行取注册表历史默认值
_DEFAULTS: dict[str, Any] = {
    'deduct_enable': False,
    'deduct_frequency': 0,
    'deduct_minmoney': '0.00',
}


def _read_setting(bind, key: str) -> Any:
    """读 settings 旧值（JSON 列经 text() 查询回来通常是字符串，统一解一层 JSON）。"""
    row = bind.execute(
        sa.text("SELECT value FROM settings WHERE `key` = :k"), {"k": key}
    ).first()
    if row is None:
        return _DEFAULTS[key]
    value = row[0]
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _truthy(value: Any) -> bool:
    """与 services/settings.setting_enabled 同款宽容判断（兼容 "1"/"true"）。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true")


def _rowcount(result) -> int:
    n = getattr(result, "rowcount", 0) or 0
    return n if n > 0 else 0


def upgrade() -> None:
    op.add_column(
        'merchants',
        sa.Column('deduct_enable', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'merchants',
        sa.Column('deduct_frequency', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'merchants',
        sa.Column('deduct_minmoney', sa.Numeric(12, 2), server_default=sa.text('0.00'), nullable=False),
    )

    bind = op.get_bind()
    old_enable = _read_setting(bind, 'deduct_enable')
    old_frequency = _read_setting(bind, 'deduct_frequency')
    old_minmoney = _read_setting(bind, 'deduct_minmoney')
    try:
        frequency = int(old_frequency or 0)
    except (TypeError, ValueError):
        frequency = 0
    try:
        # 统一按「分」两位小数落库，全程 Decimal，禁止 float 参与金额
        minmoney = str(Decimal(str(old_minmoney or 0)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        minmoney = "0.00"

    print(f"[0012] 旧全局扣量配置: deduct_enable={old_enable!r} "
          f"deduct_frequency={frequency!r} deduct_minmoney={minmoney!r}")

    if _truthy(old_enable):
        # 全部商户一起灌：三个值必须同时写，只写开关而频次留 0 = 「开着却永不扣」，比全关更难发现
        result = bind.execute(
            sa.text(
                "UPDATE merchants SET deduct_enable = 1, deduct_frequency = :f, "
                "deduct_minmoney = :m"
            ),
            {"f": frequency, "m": minmoney},
        )
        print(f"[0012] 已回填扣量配置到 {_rowcount(result)} 个商户（全部商户）")
    else:
        print("[0012] 旧全局扣量开关为关闭，不写商户行（列默认值 = 全关，与升级前一致）")

    # 旧全局 key 下架：页面已不再展示，残留行会在将来被同名 key / 导入导出意外复活
    result = bind.execute(
        sa.text(
            "DELETE FROM settings WHERE `key` IN "
            "('deduct_enable', 'deduct_frequency', 'deduct_minmoney')"
        )
    )
    print(f"[0012] 已删除旧全局扣量配置行 {_rowcount(result)} 条")


def downgrade() -> None:
    # 回滚只恢复「关闭」形态：旧全局值已并入各商户行，重放会对全平台重新打开扣量，
    # 不在这里猜测运维意图；列删除后功能随旧代码回到全局配置（需人工补齐 settings 行）。
    bind = op.get_bind()
    for key in _OLD_KEYS:
        exists = bind.execute(
            sa.text("SELECT 1 FROM settings WHERE `key` = :k"), {"k": key}
        ).first()
        if exists:
            continue
        bind.execute(
            sa.text("INSERT INTO settings (`key`, value) VALUES (:k, :v)"),
            {"k": key, "v": json.dumps(_DEFAULTS[key])},
        )
    op.drop_column('merchants', 'deduct_minmoney')
    op.drop_column('merchants', 'deduct_frequency')
    op.drop_column('merchants', 'deduct_enable')
