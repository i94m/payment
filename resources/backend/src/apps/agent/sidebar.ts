import type { Icon } from "@tabler/icons-react"
import { IconGauge, IconListDetails } from "@tabler/icons-react"

export interface NavItem {
  title: string
  url: string
  icon: Icon
}

/** 侧边栏分组（对齐老系统代理面板：首页 + 订单记录） */
export interface MenuSection {
  label?: string
  items: NavItem[]
}

// 代理端全量菜单（单一角色、无开关过滤）。保持纯静态：
// page-meta.ts 在模块加载期就要用它构建 PAGE_META。
export function getAgentMenu(): MenuSection[] {
  return [
    {
      items: [
        { title: "代理首页", url: "/", icon: IconGauge },
        { title: "订单记录", url: "/orders", icon: IconListDetails },
      ],
    },
  ]
}
