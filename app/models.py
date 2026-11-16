"""数据模型（MySQL 8 规范设计）。

设计规范：
- 引擎/字符集：InnoDB + utf8mb4（utf8mb4_0900_ai_ci），由各表 __table_args__ 统一声明
- 主键：全部使用 BIGINT 无业务含义代理主键（auto_increment，顺序插入对 InnoDB 聚簇索引友好）；
  业务键（trade_no/settle_no/out_trade_no）用唯一索引承载
- 金额：一律 Numeric(12,2) + Decimal，全链路禁止 float
- 幂等三道防线落在表结构上：orders 的唯一索引 (out_trade_no, merchant_id)、
  状态机字段、notify_status/next_notify_at 阶梯重试索引
- channel.config 存 Fernet 加密后的 JSON 字符串（密钥派生自 APP_KEY）
- 高频写表（orders/merchant_records/notify_logs）只插不改历史行，便于后续按时间分区
- 不建物理外键（高并发互联网惯例），以索引 + 服务层约束保证一致性
"""
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import ConfigDict, field_validator
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    JSON,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

# MySQL 8 表参数（sqlite 测试时自动忽略）
_MYSQL_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    # utf8mb4_unicode_ci：兼容 MySQL 5.7（0900 排序规则为 8.0 专属）
    "mysql_collate": "utf8mb4_unicode_ci",
}

# BIGINT 代理主键（sqlite 测试环境降级为 INTEGER 以支持自增）
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

# 关联列同样 BIGINT，与主键口径一致（sqlite 测试环境同步降级）
BigInt = BigInteger().with_variant(Integer, "sqlite")


def _int_to_bool(v: Any) -> Any:
    if isinstance(v, int) and not isinstance(v, bool):
        return bool(v)
    return v


def _ts_created():
    return Field(
        default=None,
        sa_column=Column(DateTime, server_default=text("CURRENT_TIMESTAMP"), nullable=False),
    )


def _ts_updated():
    # 跨方言：UPDATE 时由 SQLAlchemy 附加 NOW()
    return Field(
        default=None,
        sa_column=Column(
            DateTime,
            server_default=text("CURRENT_TIMESTAMP"),
            onupdate=func.now(),
            nullable=False,
        ),
    )


# ============================================================
# 框架表（RBAC 权限系统 + Token 管理）
# ============================================================

class SystemAdmin(SQLModel, table=True):
    __tablename__ = "system_admins"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(Integer, primary_key=True, autoincrement=True))
    username: str = Field(sa_column=Column(String(32), unique=True, nullable=False))
    name: str = Field(sa_column=Column(String(50), nullable=False))
    email: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    password: str = Field(sa_column=Column(String(255), nullable=False))
    last_active_ip: Optional[str] = Field(default=None, sa_column=Column(String(45)))
    last_active_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    is_forbid: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    is_super_admin: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)

    @field_validator("is_forbid", "is_super_admin", mode="before")
    @classmethod
    def coerce_bool(cls, v: Any) -> Any:
        return _int_to_bool(v)


class SystemPermission(SQLModel, table=True):
    __tablename__ = "system_permissions"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(Integer, primary_key=True, autoincrement=True))
    pid: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    name: str = Field(sa_column=Column(String(64), nullable=False))
    key: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


class SystemAdminPermission(SQLModel, table=True):
    __tablename__ = "system_admin_permissions"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    admin_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))
    permission_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))

    model_config = ConfigDict(from_attributes=True)


class SystemAdminLogin(SQLModel, table=True):
    __tablename__ = "system_admin_logins"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    admin_id: int = Field(sa_column=Column(Integer, nullable=False, index=True))
    ip: str = Field(sa_column=Column(String(45), nullable=False))
    user_agent: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class SystemAuditLog(SQLModel, table=True):
    """后台敏感操作审计（只增不改）。

    记录「谁 / 做了什么 / 对哪个对象 / 前后值 / 何时 / 从哪来」：
    - actor_guard/actor_id/actor_name：操作人（admin 为主，system 表示定时任务）
    - action：点分动作名，如 merchant.balance_adjust / settle.pay / plugin.install
    - target_type/target_id：被操作对象（merchant/channel/order/settlement/setting/plugin）
    - before/after：变更前后的关键字段快照（写入前已按字段名脱敏密码/密钥类键）
    - ip/user_agent/created_at：来源与时间

    与 merchant_records（资金流水）互补：流水记钱，审计记「谁下的手」。
    """
    __tablename__ = "system_audit_logs"
    __table_args__ = (
        Index("ix_system_audit_logs_action", "action", "created_at"),
        Index("ix_system_audit_logs_target", "target_type", "target_id"),
        Index("ix_system_audit_logs_actor", "actor_guard", "actor_id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    actor_guard: str = Field(default="system",
                             sa_column=Column(String(16), server_default=text("'system'"), nullable=False))
    actor_id: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    actor_name: str = Field(default="", sa_column=Column(String(64), server_default=text("''"), nullable=False))
    action: str = Field(sa_column=Column(String(64), nullable=False))
    target_type: str = Field(default="", sa_column=Column(String(32), server_default=text("''"), nullable=False))
    target_id: str = Field(default="", sa_column=Column(String(64), server_default=text("''"), nullable=False))
    before: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    after: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    ip: Optional[str] = Field(default=None, sa_column=Column(String(45)))
    user_agent: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    remark: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class PersonalAccessToken(SQLModel, table=True):
    __tablename__ = "personal_access_tokens"
    __table_args__ = (
        Index("ix_pat_guard_user_id", "guard", "user_id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    token_id: str = Field(sa_column=Column(String(64), unique=True, nullable=False, index=True))
    guard: str = Field(sa_column=Column(String(20), nullable=False))
    user_id: int = Field(sa_column=Column(BigInt, nullable=False, index=True))
    name: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    abilities: Optional[str] = Field(default=None, sa_column=Column(Text))
    ip: Optional[str] = Field(default=None, sa_column=Column(String(45)))
    user_agent: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    last_used_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    expires_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 商户
# ============================================================

class Merchant(SQLModel, table=True):
    """商户。

    pid 对外暴露（开放 API 认证的 pid 参数）；
    API 调用须先配置 RSA 公钥（public_key，商户面板一键生成密钥对）。
    balance 热点行：仅允许 services/balance.change_merchant_balance（行锁+流水）变更。
    """
    __tablename__ = "merchants"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    pid: int = Field(sa_column=Column(Integer, unique=True, nullable=False))
    username: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    password: str = Field(sa_column=Column(String(255), nullable=False))  # bcrypt
    public_key: Optional[str] = Field(default=None, sa_column=Column(Text))
    balance: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))
    frozen_balance: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))
    status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 1=正常 0=禁用
    # 商户级费率覆盖（平台抽成 %），空则用通道 merchant_rate
    rate: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(5, 2)))
    default_notify_url: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    default_return_url: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    email: Optional[str] = Field(default=None, sa_column=Column(String(128)))
    phone: Optional[str] = Field(default=None, sa_column=Column(String(20)))
    # 结算收款信息
    settle_account: Optional[str] = Field(default=None, sa_column=Column(String(128)))
    settle_name: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    settle_type: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 1支付宝 2微信 3QQ 4银行卡
    auto_settle: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    # 预留余额：独立口径，结算可提时扣留
    remain_money: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))
    # 邀请返现：invite_code 对外分发，invited_by=邀请人商户 id（0=无）
    invite_code: Optional[str] = Field(default=None, sa_column=Column(String(16), unique=True))
    invited_by: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    # 消息订阅开关（邮件通道由 站点配置 mail_channel 决定是否实际发信；站内消息始终送达）
    notify_order: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    notify_settle: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    notify_login: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    notify_lowbalance: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    # 支付/结算独立开关（pay 2=待审核）
    pay_status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 0=关闭 1=正常 2=待审核
    settle_status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 0=关闭 1=正常
    # 联系方式 / 注册域名 / 域名白名单（空=不限制，逗号分隔）
    qq: Optional[str] = Field(default=None, sa_column=Column(String(32)))
    url: Optional[str] = Field(default=None, sa_column=Column(String(200)))
    allowed_domains: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    # 随机金额 / 限制拉单（防刷单定制）
    random_enable: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    random_min: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))
    random_max: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))
    random_ratelimit: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))  # 同IP每分钟建单上限 0=不限
    # 固定保护：不受批量随机金额影响
    fixed: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    last_login_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


class MerchantRecord(SQLModel, table=True):
    """商户资金流水（唯一记账出口 services/balance.py 写入，只增不改）。

    全部 biz_type 记 balance_before/after（余额口径快照）。
    """
    __tablename__ = "merchant_records"
    __table_args__ = (
        Index("ix_records_merchant_id", "merchant_id", "id"),
        Index("ix_records_order", "order_trade_no"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    biz_type: str = Field(sa_column=Column(String(32), nullable=False))  # order_income/refund/settle/settle_reject/adjust/recharge/invite_rebate
    amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))  # 正增负减
    balance_before: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    balance_after: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    order_trade_no: Optional[str] = Field(default=None, sa_column=Column(String(32)))
    remark: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 支付方式 / 插件 / 通道
# ============================================================

class PaymentType(SQLModel, table=True):
    """对外支付方式（开放 API type 参数：alipay/wxpay/qqpay/bank...）。"""
    __tablename__ = "payment_types"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(Integer, primary_key=True, autoincrement=True))
    code: str = Field(sa_column=Column(String(20), unique=True, nullable=False))
    name: str = Field(sa_column=Column(String(32), nullable=False))
    # 支持设备：0=PC+Mobile 1=PC 2=Mobile
    device: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))
    icon: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))
    sort: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class Plugin(SQLModel, table=True):
    """支付插件注册表快照（admin「同步插件」按 PluginRegistry 扫描的插件类 meta 写入）。"""
    __tablename__ = "plugins"
    __table_args__ = _MYSQL_ARGS

    name: str = Field(sa_column=Column(String(40), primary_key=True))
    showname: str = Field(sa_column=Column(String(64), nullable=False))
    supported_types: Optional[str] = Field(default=None, sa_column=Column(String(255)))  # 逗号分隔 payment_type.code
    # 凭证表单 schema：[{name,title,placeholder,required,type}]，admin 动态渲染
    inputs_schema: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    is_enabled: bool = Field(default=True, sa_column=Column(Boolean, server_default=text("1"), nullable=False))
    version: Optional[str] = Field(default=None, sa_column=Column(String(16)))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


class Channel(SQLModel, table=True):
    """上游通道：一个插件实例 + 一份加密凭证 + 路由参数。"""
    __tablename__ = "channels"
    __table_args__ = (
        Index("ix_channels_type_status", "payment_type_id", "status"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    name: str = Field(sa_column=Column(String(64), nullable=False))
    plugin_name: str = Field(sa_column=Column(String(40), nullable=False))
    payment_type_id: int = Field(sa_column=Column(Integer, nullable=False))
    merchant_rate: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(5, 2), server_default=text("0.00"), nullable=False))  # 平台抽成%
    cost_rate: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(5, 2), server_default=text("0.00"), nullable=False))  # 成本费率%
    status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 1=启用 0=停用
    mode: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))  # 0=随机 1=顺序 2=权重
    weight: int = Field(default=1, sa_column=Column(Integer, server_default=text("1"), nullable=False))
    min_amount: Decimal = Field(default=Decimal("0.01"), sa_column=Column(Numeric(12, 2), server_default=text("0.01"), nullable=False))
    max_amount: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))  # 0=不限
    day_limit: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))  # 当日限额 0=不限
    day_limit_count: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))  # 当日限笔 0=不限
    config: Optional[str] = Field(default=None, sa_column=Column(Text))  # Fernet 加密 JSON
    sort: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    debug_log: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))  # 调试日志 0=关 1=开
    usetime: int = Field(default=0, sa_column=Column(BigInteger, server_default=text("0"), nullable=False))  # 顺序轮询游标
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 订单
# ============================================================

class Order(SQLModel, table=True):
    """支付订单。

    状态机：0 待支付 → 1 已支付 → 2 已退款 / 3 已过期。
    notify_status：0=通知成功 1..n=待重试 -1=重试耗尽（仅已支付订单有意义）。
    高频写路径索引：下单幂等唯一键、商户列表组合索引、通知扫描索引、过期扫描索引。
    """
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("out_trade_no", "merchant_id", name="uq_orders_out_trade_no_merchant"),
        Index("ix_orders_merchant_created", "merchant_id", "status", "created_at"),
        # 代理业绩聚合（merchant_id + agent_id 等值 + created_at 范围）
        Index("ix_orders_merchant_agent_created", "merchant_id", "agent_id", "created_at"),
        Index("ix_orders_notify_retry", "notify_status", "next_notify_at"),
        Index("ix_orders_status_expire", "status", "expire_at"),
        Index("ix_orders_created", "created_at"),
        # 通道限速回退检查 / 管理端按通道统计与筛选（channel_id 等值 + created_at 范围）
        Index("ix_orders_channel_created", "channel_id", "created_at"),
        # 商户同 IP 拉单限流计数（merchant_id + client_ip 等值 + created_at 范围）
        Index("ix_orders_merchant_ip_created", "merchant_id", "client_ip", "created_at"),
        # 未支付订单主动查单扫描（status=0 AND created_at >= ? ORDER BY created_at）
        Index("ix_orders_status_created", "status", "created_at"),
        # 商户仪表盘/趋势图按实付时间聚合（merchant_id 等值 + paid_at 范围）：
        # 原先只能吃 (merchant_id,status) 前缀全扫商户已付单，20 万行实测 45666→179 行
        Index("ix_orders_merchant_paid", "merchant_id", "paid_at"),
        # 全站口径的「已支付 + 实付时间」聚合（admin 看板/今日收入/趋势图）：
        # 原实现没有以 status 为前导、paid_at 为次列的索引，全站查询退化为扫全部
        # 已支付行；20 万行造数实测今日收入 14 万行/72.7ms → 1111 行/1.3ms
        # （见迁移 0004 的 EXPLAIN 对比）
        Index("ix_orders_status_paid", "status", "paid_at"),
        # 代理面板订单列表/计数（agent_id 不在其他索引首列，原计数扫 6.6 万行）
        Index("ix_orders_agent_created", "agent_id", "created_at"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    trade_no: str = Field(sa_column=Column(String(22), unique=True, nullable=False))  # yymmddHHMMSSffffff+4随机
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    out_trade_no: str = Field(sa_column=Column(String(64), nullable=False))
    api_trade_no: Optional[str] = Field(default=None, sa_column=Column(String(64)))  # 上游单号
    payment_type_id: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    payment_type_code: str = Field(default="", sa_column=Column(String(20), nullable=False, server_default=text("''")))
    channel_id: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    plugin_name: Optional[str] = Field(default=None, sa_column=Column(String(40)))
    subject: str = Field(sa_column=Column(String(128), nullable=False))
    money: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))  # 商户申报金额
    realmoney: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))  # 买家实付
    merchant_income: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))  # 商户到账
    channel_cost: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))  # 通道成本
    profit: Optional[Decimal] = Field(default=None, sa_column=Column(Numeric(12, 2)))  # 平台利润
    notify_url: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    return_url: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    param: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    status: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))
    notify_status: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))
    notify_count: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))
    next_notify_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    paid_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    expire_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    client_ip: Optional[str] = Field(default=None, sa_column=Column(String(45)))
    buyer_id: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    agent_id: int = Field(default=0, sa_column=Column(BigInt, server_default=text("0"), nullable=False))  # 归属代理（agents.id，0=无；代理业绩口径）
    order_type: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))  # 0=普通收款 1=余额充值（全额入账、不回调）
    is_deducted: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))  # 命中扣量（商户侧一律显示未支付）
    extra: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


class Refund(SQLModel, table=True):
    """订单退款单（开放 API 商户发起，同步退款模型）。

    status：0 处理中 / 1 成功 / 2 失败 —— 同步模型下插件返回即落终态（成功=1）。
    (merchant_id, out_refund_no) 唯一：商户退款幂等键（同键重复提交返回原单）。
    可退上限 = merchant_income（NULL 兜底 money）；退满置 order.status=2。
    """
    __tablename__ = "refunds"
    __table_args__ = (
        UniqueConstraint("merchant_id", "out_refund_no", name="uq_refunds_merchant_out_refund_no"),
        Index("ix_refunds_trade", "trade_no"),
        Index("ix_refunds_merchant", "merchant_id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    refund_no: str = Field(sa_column=Column(String(22), unique=True, nullable=False))  # R+yymmddHHMMSSffffff = 22 位
    out_refund_no: Optional[str] = Field(default=None, sa_column=Column(String(64)))  # 商户退款幂等键
    trade_no: str = Field(sa_column=Column(String(22), nullable=False))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))
    status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))
    reason: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    result: Optional[str] = Field(default=None, sa_column=Column(Text))  # 插件返回原文留痕
    created_at: Optional[datetime] = _ts_created()
    finished_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 结算
# ============================================================

class Settlement(SQLModel, table=True):
    """商户结算单。状态：0 待处理 / 1 已打款 / 2 已拒绝。"""
    __tablename__ = "settlements"
    __table_args__ = (
        Index("ix_settlements_merchant_status", "merchant_id", "status"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    settle_no: str = Field(sa_column=Column(String(24), unique=True, nullable=False))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))  # 申请金额
    fee: Decimal = Field(default=Decimal("0.00"), sa_column=Column(Numeric(12, 2), server_default=text("0.00"), nullable=False))
    real_amount: Decimal = Field(sa_column=Column(Numeric(12, 2), nullable=False))  # 实打金额
    settle_type: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 1支付宝 2微信 3QQ 4银行卡
    account: str = Field(default="", sa_column=Column(String(128), nullable=False, server_default=text("''")))
    account_name: str = Field(default="", sa_column=Column(String(64), nullable=False, server_default=text("''")))
    status: int = Field(default=0, sa_column=Column(SmallInteger, server_default=text("0"), nullable=False))
    remark: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    paid_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 回调日志（全量留痕，只增不改）
# ============================================================

class NotifyLog(SQLModel, table=True):
    """商户/上游 HTTP 回调留痕。kind：merchant=通知商户 upstream=上游回调。"""
    __tablename__ = "notify_logs"
    __table_args__ = (
        Index("ix_notify_logs_trade", "trade_no", "id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    kind: str = Field(sa_column=Column(String(20), nullable=False))
    trade_no: str = Field(sa_column=Column(String(22), nullable=False))
    url: str = Field(sa_column=Column(String(500), nullable=False))
    request: Optional[str] = Field(default=None, sa_column=Column(Text))
    response: Optional[str] = Field(default=None, sa_column=Column(Text))
    http_status: Optional[int] = Field(default=None, sa_column=Column(Integer))
    attempt: int = Field(default=1, sa_column=Column(Integer, server_default=text("1"), nullable=False))
    success: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("0"), nullable=False))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 商户侧扩展：代理 / 公告 / 登录日志 / 站内消息
# ============================================================

class Agent(SQLModel, table=True):
    """下级代理：商户自建，专属收款链接带来的订单记 agent_id。

    name 全局唯一（代理面板凭 name+password 登录，全局唯一才不产生身份歧义，
    唯一索引同时承载登录查询）。删除为软删除：status 置 0 + deleted_at 落值 +
    name 追加 #del{id} 释放原名称，行保留使历史订单业绩聚合不丢。
    """
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_merchant", "merchant_id", "id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    name: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    password: str = Field(sa_column=Column(String(255), nullable=False))  # bcrypt
    status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 1=启用 0=停用（含软删除）
    remark: Optional[str] = Field(default=None, sa_column=Column(String(255)))
    # 软删除标记（NULL=未删除；删除时同步 status=0）
    deleted_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    last_login_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


class PayToken(SQLModel, table=True):
    """收款页短码：/p/{token} → (merchant_id, agent_id)。

    替代 Fernet 长令牌（100 字符 → 10 字符随机 base62）；短码不携带任何
    业务号，唯一索引反查。惰性创建：商户/代理面板首次取链接时落库，
    终身复用；重置链接 = 换码（改 token 列即可，旧链立即失效）。
    agent_id=0 表示商户自身收款页，>0 为下级代理专属链接。
    """
    __tablename__ = "pay_tokens"
    __table_args__ = (
        UniqueConstraint("merchant_id", "agent_id", name="uq_pay_tokens_merchant_agent"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    token: str = Field(sa_column=Column(String(12), unique=True, nullable=False))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    agent_id: int = Field(default=0, sa_column=Column(BigInt, server_default=text("0"), nullable=False))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class Announcement(SQLModel, table=True):
    """平台公告（无标题，仅内容；商户端用户中心顶部展示）。"""
    __tablename__ = "announcements"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    content: str = Field(sa_column=Column(Text, nullable=False))
    status: int = Field(default=1, sa_column=Column(SmallInteger, server_default=text("1"), nullable=False))  # 1=发布 0=下线
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)


class MerchantLoginLog(SQLModel, table=True):
    """商户登录日志。"""
    __tablename__ = "merchant_logins"
    __table_args__ = (
        Index("ix_merchant_logins_merchant", "merchant_id", "id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    ip: str = Field(default="", sa_column=Column(String(45), nullable=False, server_default=text("''")))
    user_agent: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class MerchantMessage(SQLModel, table=True):
    """站内消息（结算/充值结果、公告等；邮件按订阅开关叠加）。"""
    __tablename__ = "merchant_messages"
    __table_args__ = (
        Index("ix_merchant_messages_merchant", "merchant_id", "id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    merchant_id: int = Field(sa_column=Column(BigInt, nullable=False))
    title: str = Field(sa_column=Column(String(128), nullable=False))
    content: str = Field(default="", sa_column=Column(String(500), nullable=False, server_default=text("''")))
    biz_type: str = Field(default="system", sa_column=Column(String(32), nullable=False, server_default=text("'system'")))  # system/settle/recharge
    read_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class AdminMessage(SQLModel, table=True):
    """后台通知中心（admin 站内通知）。

    面向全体管理员，已读为全局语义：任一管理员标记已读即对所有人已读
    （运营小团队约定，无需按管理员分别记录）。
    写入方：send_admin_alert（队列监控告警等系统巡检）。
    """
    __tablename__ = "admin_messages"
    __table_args__ = (
        Index("ix_admin_messages_read", "read_at", "id"),
        _MYSQL_ARGS,
    )

    id: int = Field(sa_column=Column(BigIntPK, primary_key=True, autoincrement=True))
    title: str = Field(sa_column=Column(String(128), nullable=False))
    content: str = Field(default="", sa_column=Column(String(500), nullable=False, server_default=text("''")))
    biz_type: str = Field(default="alert", sa_column=Column(String(32), nullable=False, server_default=text("'alert'")))  # alert/system
    read_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime))
    created_at: Optional[datetime] = _ts_created()

    model_config = ConfigDict(from_attributes=True)


class Counter(SQLModel, table=True):
    """平台计数器（行级 FOR UPDATE 保证原子）。

    目前用于扣量轮询计数（key='deduct_order'）。
    """
    __tablename__ = "counters"
    __table_args__ = _MYSQL_ARGS

    key: str = Field(sa_column=Column(String(64), primary_key=True))
    value: int = Field(default=0, sa_column=Column(Integer, server_default=text("0"), nullable=False))

    model_config = ConfigDict(from_attributes=True)


# ============================================================
# 运行期设置 KV
# ============================================================

class Setting(SQLModel, table=True):
    """运行期设置 — KV 行式:一行一个配置项,value 为 JSON。

    合法 key / 类型 / 默认值 / 分组见 app/constants/settings.py 注册表;
    每行 updated_at 即该项配置的最后修改时间。
    """
    __tablename__ = "settings"
    __table_args__ = _MYSQL_ARGS

    id: int = Field(sa_column=Column(Integer, primary_key=True, autoincrement=True))
    key: str = Field(sa_column=Column(String(64), unique=True, nullable=False))
    value: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    created_at: Optional[datetime] = _ts_created()
    updated_at: Optional[datetime] = _ts_updated()

    model_config = ConfigDict(from_attributes=True)
