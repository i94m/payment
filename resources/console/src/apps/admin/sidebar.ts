import {
  IconGauge, IconListDetails, IconBuildingStore, IconHistory,
  IconReceipt, IconSpeakerphone, IconRoute,
  IconAdjustments, IconPlugConnected, IconBell,
} from "@tabler/icons-react"
import type { MenuSection } from "@/apps/merchant/sidebar"

// 与商户端一致的平铺分组菜单（分组标题常驻、无折叠子菜单），
// permission 与后端 PERMISSION_TREE / MENU_TREE 的 key 对齐
export function getAdminMenu(): MenuSection[] {
  return [
    {
      items: [
        { title: "仪表盘", url: "/", icon: IconGauge },
        { title: "通知中心", url: "/system/notifications", icon: IconBell },
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
        { title: "公告管理", url: "/announces", icon: IconSpeakerphone, permission: "announce.view" },
      ],
    },
    {
      label: "系统配置",
      items: [
        { title: "支付通道", url: "/pay/channels", icon: IconRoute, permission: "channel.view" },
        { title: "站点配置", url: "/setting/pay", icon: IconAdjustments, permission: "pay_setting.view" },
      ],
    },
  ]
}
