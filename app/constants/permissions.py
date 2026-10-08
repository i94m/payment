"""权限树定义

格式: (name, key, [(子权限name, key), ...])

修改后运行: uv run python artisan.py permission sync
"""

PERMISSION_TREE: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "仪表盘", "dashboard", [
            ("查看", "dashboard.view"),
        ]
    ),
    (
        "订单管理", "order", [
            ("查看", "order.view"),
            ("导出", "order.export"),
            ("手动补单", "order.refill"),
            ("重发通知", "order.notify"),
        ]
    ),
    (
        "商户管理", "merchant", [
            ("查看", "merchant.view"),
            ("创建", "merchant.create"),
            ("编辑", "merchant.update"),
            ("调整余额", "merchant.balance"),
            ("重置密钥", "merchant.resetkey"),
            ("删除", "merchant.delete"),
            ("导出", "merchant.export"),
            ("SSO登录", "merchant.sso"),
        ]
    ),
    (
        "支付通道", "channel", [
            ("查看", "channel.view"),
            ("创建", "channel.create"),
            ("编辑", "channel.update"),
            ("删除", "channel.delete"),
        ]
    ),
    (
        "支付方式", "payment_type", [
            ("查看", "payment_type.view"),
            ("创建", "payment_type.create"),
            ("编辑", "payment_type.update"),
            ("删除", "payment_type.delete"),
        ]
    ),
    (
        "结算管理", "settle", [
            ("查看", "settle.view"),
            ("打款", "settle.pay"),
            ("拒绝", "settle.reject"),
        ]
    ),
    (
        "公告管理", "announce", [
            ("查看", "announce.view"),
            ("创建", "announce.create"),
            ("编辑", "announce.update"),
            ("删除", "announce.delete"),
        ]
    ),
    (
        "回调日志", "notify_log", [
            ("查看", "notify_log.view"),
        ]
    ),
    (
        "队列监控", "queue", [
            ("查看", "queue.view"),
        ]
    ),
    (
        "支付设置", "pay_setting", [
            ("查看", "pay_setting.view"),
            ("更新", "pay_setting.update"),
        ]
    ),
    (
        "系统管理", "system", [
            ("管理员账号", "system.admin"),
        ]
    ),
]

# 前端菜单（MENU_TREE）：key 对应权限 key，前端按权限过滤显隐
MENU_TREE: list[dict] = [
    {"key": "dashboard.view", "name": "数据看板", "path": "/dashboard", "icon": "icon-dashboard"},
    {"key": "order.view", "name": "订单管理", "path": "/trade/orders", "icon": "icon-list"},
    {"key": "merchant.view", "name": "商户管理", "path": "/merchants", "icon": "icon-user"},
    {"key": "merchant.view", "name": "资金明细", "path": "/records", "icon": "icon-file"},
    {"key": "channel.view", "name": "支付通道", "path": "/pay/channels", "icon": "icon-swap"},
    {"key": "payment_type.view", "name": "支付方式", "path": "/pay/types", "icon": "icon-cloud"},
    {"key": "settle.view", "name": "结算管理", "path": "/settle", "icon": "icon-thumb-up"},
    {"key": "announce.view", "name": "公告管理", "path": "/announces", "icon": "icon-speaker"},
    {"key": "notify_log.view", "name": "回调日志", "path": "/trade/notifylogs", "icon": "icon-file"},
    {"key": "pay_setting.view", "name": "支付设置", "path": "/setting/pay", "icon": "icon-settings"},
    {"key": "system.admin", "name": "管理员账号", "path": "/system/accounts", "icon": "icon-safe"},
]
