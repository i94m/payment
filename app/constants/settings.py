"""运行期支付设置注册表 — 合法 key / 类型 / 默认值 / 分组的唯一事实来源。

settings 表为 KV 行式:一行一个配置项,库里只存「被改过」的 key,
缺行即取本注册表的默认值。新增 key 只需在 SETTINGS_REGISTRY 加一条。

类型说明:
- DECIMAL 金额类:JSON 里存字符串("1.00"),读端经 get_money_setting 转 Decimal
  ——全链路禁止 float,见 models.py 金额规范
- LIST_INT:JSON 里存整数数组,读端直接得到 list[int]

注意:key 一律小写 snake_case(模块底部有注册期校验)。
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any


class SettingType(StrEnum):
    STRING = "string"   # 单行文本
    TEXT = "text"       # 多行文本
    INT = "int"
    FLOAT = "float"
    DECIMAL = "decimal" # 金额:JSON 存字符串,读端转 Decimal
    BOOL = "bool"
    LIST = "list"       # 字符串数组
    LIST_INT = "list_int"  # 正整数数组


class SettingGroup(StrEnum):
    SITE = "site"      # 站点
    ORDER = "order"    # 订单
    NOTIFY = "notify"  # 商户通知
    SETTLE = "settle"  # 结算


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: SettingType
    default: Any
    group: SettingGroup
    label: str  # 中文说明(前端渲染)
    description: str = ""  # 补充描述(前端渲染在标签下方,可空)


SETTINGS_REGISTRY: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in [
        # ---- site ----
        SettingSpec("site_name", SettingType.STRING, "聚合支付", SettingGroup.SITE, "站点名称"),
        SettingSpec("site_url", SettingType.STRING, "", SettingGroup.SITE,
                    "站点地址（用于构建回调/回跳地址，留空自动取请求地址）"),
        SettingSpec("platform_rsa_private_key", SettingType.TEXT, "", SettingGroup.SITE,
                    "平台RSA私钥（Base64；API响应/异步通知签名用，留空则下次启动自动生成）"),
        # ---- order ----
        SettingSpec("order_expire_minutes", SettingType.INT, 30, SettingGroup.ORDER, "订单过期时间（分钟）"),
        SettingSpec("query_unpaid_enable", SettingType.BOOL, True, SettingGroup.ORDER, "掉单主动查单开关"),
        SettingSpec("query_unpaid_window", SettingType.INT, 10, SettingGroup.ORDER,
                    "主动查单窗口（分钟：扫 expire_at 不早于 now-N 的待支付单，订单有效期外再宽限 N 分钟）"),
        SettingSpec("late_pay_backfill_hours", SettingType.INT, 24, SettingGroup.ORDER,
                    "过期单回补窗口（小时，0=关闭；过期后该窗口内仍接受已验签的回调/查单入账，"
                    "窗口外的历史单只能后台手动补单）"),
        SettingSpec("invite_rebate_rate", SettingType.DECIMAL, "0.00", SettingGroup.ORDER,
                    "邀请返现比例（%，按订单到账计算，0=关闭）"),
        SettingSpec("recharge_enable", SettingType.BOOL, True, SettingGroup.ORDER, "商户余额充值开关"),
        SettingSpec("merchant_reg_open", SettingType.BOOL, False, SettingGroup.SITE, "商户自助注册开关"),
        SettingSpec("merchant_reg_review", SettingType.BOOL, False, SettingGroup.SITE,
                    "注册商户需审核（开启后新注册商户支付权限为待审核）"),
        SettingSpec("test_pay_enable", SettingType.BOOL, True, SettingGroup.ORDER, "通道测试支付开关"),
        SettingSpec("test_pay_merchant_pid", SettingType.INT, 0, SettingGroup.ORDER,
                    "测试支付商户 PID",
                    description="通道列表「测试」按钮的挂单商户 PID，必须是已启用商户的 PID；0=未配置，通道测试不可用"),
        SettingSpec("deduct_enable", SettingType.BOOL, False, SettingGroup.ORDER, "扣量开关",
                    description="开启后按成功订单频次轮询抽单，商户侧显示为未支付"),
        SettingSpec("deduct_frequency", SettingType.INT, 0, SettingGroup.ORDER,
                    "扣量频率（每 N 笔扣 1，0=不扣）"),
        SettingSpec("deduct_minmoney", SettingType.DECIMAL, "0.00", SettingGroup.ORDER,
                    "豁免金额（订单≤该值不扣，0=不豁免）"),
        # ---- notify ----
        SettingSpec("notify_retry_ladder", SettingType.LIST_INT,
                    [1, 2, 5, 10, 30, 60, 120], SettingGroup.NOTIFY,
                    "商户通知重试阶梯（分钟）"),
        SettingSpec("notify_max_attempts", SettingType.INT, 6, SettingGroup.NOTIFY, "商户通知最大尝试次数"),
        SettingSpec("notify_fail_block_enable", SettingType.BOOL, False, SettingGroup.NOTIFY,
                    "回调连续失败自动关闭商户支付权限（风控联动）"),
        SettingSpec("notify_fail_block_threshold", SettingType.INT, 10, SettingGroup.NOTIFY,
                    "24 小时内通知失败订单数达到该值自动关闭支付权限"),
        SettingSpec("mail_channel", SettingType.STRING, "", SettingGroup.NOTIFY,
                    "邮件插件名（smtp/aliyun，留空=关闭站内邮件发送）"),
        SettingSpec("mail_config", SettingType.TEXT, "", SettingGroup.NOTIFY,
                    "邮件插件配置（JSON，结构与所选插件 inputs 一致）"),
        SettingSpec("alert_queue_enable", SettingType.BOOL, True, SettingGroup.NOTIFY,
                    "队列监控告警开关（通知重试耗尽/任务失败/僵死回收/积压）"),
        SettingSpec("alert_queue_backlog", SettingType.INT, 500, SettingGroup.NOTIFY,
                    "队列积压告警阈值（queued+scheduled 超过即告警）"),
        # ---- settle ----
        SettingSpec("merchant_default_rate", SettingType.DECIMAL, "0.00", SettingGroup.SETTLE, "新商户默认费率（%）"),
        SettingSpec("settle_min_amount", SettingType.DECIMAL, "1.00", SettingGroup.SETTLE, "结算最低金额（元）"),
        SettingSpec("settle_fee_rate", SettingType.DECIMAL, "0.00", SettingGroup.SETTLE, "结算手续费率（%）"),
        SettingSpec("settle_fee_min", SettingType.DECIMAL, "0.00", SettingGroup.SETTLE, "结算手续费保底（元，0=不保底）"),
        SettingSpec("settle_fee_max", SettingType.DECIMAL, "0.00", SettingGroup.SETTLE, "结算手续费封顶（元，0=不封顶）"),
        SettingSpec("settle_daily_limit", SettingType.INT, 0, SettingGroup.SETTLE, "每日结算申请次数上限（0=不限）"),
        SettingSpec("settle_d1_enable", SettingType.BOOL, False, SettingGroup.SETTLE,
                    "D+1 结算（开启后当日收款不可提现，次日可提）"),
        SettingSpec("auto_settle_enable", SettingType.BOOL, False, SettingGroup.SETTLE,
                    "自动结算开关（开启后 cron 按商户 auto_settle 标记批量发起）"),
        # ---- site ----
        SettingSpec("paypage_enable", SettingType.BOOL, True, SettingGroup.SITE, "聚合收款页开关"),
        SettingSpec("landing_enable", SettingType.BOOL, True, SettingGroup.SITE, "宣传首页开关",
                    description="关闭后站点根路径 / 不跳转，改为返回极简占位页：居中显示站点名称 + 「商户登录」链接（指向 /merchant/）"),
        SettingSpec("api_pay_enable", SettingType.BOOL, True, SettingGroup.SITE,
                    "下游API支付接口开关（关闭后开放API拒绝下单/查单，商户端隐藏「API信息」菜单）"),
        SettingSpec("api_refund_enable", SettingType.BOOL, True, SettingGroup.SITE,
                    "商户退款API开关（关闭后开放API拒绝退款）"),
        SettingSpec("api_order_rate_limit", SettingType.INT, 60, SettingGroup.ORDER,
                    "开放API下单限流（次/分钟/来源IP，0=不限）"),
        # ---- 商户端菜单显隐 ----
        SettingSpec("agent_enable", SettingType.BOOL, True, SettingGroup.SITE, "下级代理开关（商户端「下级代理」菜单整组）"),
        SettingSpec("announce_enable", SettingType.BOOL, True, SettingGroup.SITE, "商户公告开关",
                    description="关闭后商户端用户中心不展示公告，管理端也不提供发布入口；仅可在数据库/接口修改"),
        SettingSpec("site_qq_group", SettingType.STRING, "", SettingGroup.SITE, "产品QQ群链接（商户端菜单外链，留空隐藏）"),
        SettingSpec("site_app_url", SettingType.STRING, "", SettingGroup.SITE, "APP下载链接（商户端菜单外链，留空隐藏）"),
    ]
}

# 站点配置页可见 key 白名单：站点名称 + 订单回补窗口 + 扣量 + 结算组（自动结算 / D+1 除外）。
# 注册开关、api_pay_enable / landing_enable / api_order_rate_limit 等不在页面上暴露
# （仍由注册表提供默认值、运行期生效，可直接写库或 PATCH 接口调整），
# 其余项同理只是不在设置页展示/编辑。
ADMIN_SETTING_HIDDEN_KEYS: set[str] = {"auto_settle_enable", "settle_d1_enable"}
ADMIN_SETTING_ORDER_KEYS: set[str] = {"late_pay_backfill_hours"}
# 通道测试支付（admin 通道列表「测试」用）：开关 + 挂单商户 PID。
# 必须放进白名单，否则配置页看不到、PATCH 也会被拒（历史上漏放，导致
# 通道测试一直报「请先在站点配置中配置测试商户PID」）。
ADMIN_SETTING_TEST_PAY_KEYS: set[str] = {"test_pay_enable", "test_pay_merchant_pid"}
ADMIN_SETTING_KEYS: set[str] = (
    {"site_name"}
    | ADMIN_SETTING_ORDER_KEYS
    | ADMIN_SETTING_TEST_PAY_KEYS
    | {k for k in SETTINGS_REGISTRY if k.startswith("deduct_")}
    | {k for k, spec in SETTINGS_REGISTRY.items() if spec.group is SettingGroup.SETTLE}
) - ADMIN_SETTING_HIDDEN_KEYS

# /site-info 公开下发的功能开关白名单（商户端菜单显隐、注册入口显隐）。
# 仅布尔入口开关 + 两条外链字符串，敏感配置绝不入列。
PUBLIC_FEATURE_KEYS: list[str] = [
    "agent_enable", "announce_enable",
    "recharge_enable", "paypage_enable", "api_pay_enable", "merchant_reg_open",
    "site_qq_group", "site_app_url",
]

# key 格式:小写 snake_case,2~64 字符
SETTING_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _validate_registry() -> None:
    """注册期自检 — key 重复 / 格式非法直接 fail-fast。"""
    seen: set[str] = set()
    for key in SETTINGS_REGISTRY:
        if key in seen:
            raise ValueError(f"SETTINGS_REGISTRY 重复 key: {key}")
        seen.add(key)
        if not SETTING_KEY_RE.match(key):
            raise ValueError(f"SETTINGS_REGISTRY 非法 key(需小写 snake_case): {key}")


_validate_registry()
