import {
  IconGauge, IconListDetails, IconBuildingStore, IconHistory,
  IconReceipt, IconSpeakerphone, IconRoute,
  IconAdjustments, IconPlugConnected, IconBell, IconShieldCheck,
  IconCreditCard, IconShieldLock,
} from "@tabler/icons-react"
import type { MenuSection } from "@/apps/merchant/sidebar"
import { FEATURE_NOTIFICATION_CENTER } from "@/lib/features"

// 与商户端一致的平铺分组菜单（分组标题常驻、无折叠子菜单），
// permission 与后端 PERMISSION_TREE / MENU_TREE 的 key 对齐
export function getAdminMenu(): MenuSection[] {
  return [
    {
      items: [
        { title: "数据统计", url: "/", icon: IconGauge },
        // 通知中心入口由 FEATURE_NOTIFICATION_CENTER 控制：关闭时仅隐藏入口，
        // 页面 / 路由 / 接口代码保留（hidden 不影响 page-meta 推导页面标题）
        {
          title: "通知中心", url: "/system/notifications", icon: IconBell,
          hidden: !FEATURE_NOTIFICATION_CENTER,
        },
      ],
    },
    {
      label: "交易管理",
      items: [
        { title: "订单管理", url: "/trade/orders", icon: IconListDetails, permission: "order.view" },
        { title: "回调日志", url: "/trade/notifylogs", icon: IconPlugConnected, permission: "notify_log.view" },
      ],
    },
    {
      label: "商户管理",
      items: [
        { title: "商户列表", url: "/merchants", icon: IconBuildingStore, permission: "merchant.view" },
        { title: "资金明细", url: "/records", icon: IconHistory, permission: "merchant.view" },
        { title: "结算管理", url: "/settle", icon: IconReceipt, permission: "settle.view" },
        // 商户公告开关（announce_enable，默认开启，仅库内可改）：关闭后不出现发布入口，
        // 页面/路由/接口代码保留（直链落到 404）
        {
          title: "公告管理", url: "/announces", icon: IconSpeakerphone,
          permission: "announce.view", feature: "announce_enable",
        },
      ],
    },
    {
      label: "系统配置",
      items: [
        { title: "支付通道", url: "/pay/channels", icon: IconRoute, permission: "channel.view" },
        { title: "支付方式", url: "/pay/types", icon: IconCreditCard, permission: "payment_type.view" },
        { title: "站点配置", url: "/setting/pay", icon: IconAdjustments, permission: "pay_setting.view" },
        // 管理员账号（RBAC）与后端 MENU_TREE 对齐；超管与有 system.admin 的子账号可见
        { title: "管理员账号", url: "/system/accounts", icon: IconShieldLock, permission: "system.admin" },
      ],
    },
    {
      label: "系统管理",
      items: [
        // 审计日志：只读，仅授予 system.audit 的账号可见（默认超管）
        { title: "审计日志", url: "/system/audit", icon: IconShieldCheck, permission: "system.audit" },
      ],
    },
  ]
}
