import type { Icon } from "@tabler/icons-react"
import {
  IconGauge, IconListDetails, IconReceipt, IconHistory,
  IconShieldLock, IconBook,
  IconUsers,
  IconUserEdit, IconLock,
  IconChartBar, IconBrandQq, IconDeviceMobile,
} from "@tabler/icons-react"
import type { SiteFeatures } from "@/stores/site-store"

export interface NavItem {
  title: string
  url: string
  icon: Icon
  permission?: string
  /** 外链（新窗口打开，不参与 active 高亮） */
  external?: boolean
  /** 关联的功能开关，为 false 时隐藏该项 */
  feature?: keyof SiteFeatures
  /** 静态隐藏：不出现在侧边栏，页面与路由保留（页面元信息仍按全量菜单推导） */
  hidden?: boolean
  /** url 取自站点设置（如 QQ群/APP下载外链），值为空时隐藏 */
  urlFeature?: keyof SiteFeatures
}

/** 侧边栏分组（首组无标签，其余为「个人资料/查询/下级代理/其他」分区） */
export interface MenuSection {
  label?: string
  /** 整组关联的功能开关，为 false 时隐藏整组 */
  feature?: keyof SiteFeatures
  items: NavItem[]
}

// 商户端全量菜单（显隐开关与后端
// merchant/common.menuList 的 flag 一致）。保持纯静态：page-meta.ts 在模块
// 加载期（开关未拉取）就要用它构建 PAGE_META，过滤只在渲染时做。
export function getMerchantMenu(): MenuSection[] {
  return [
    {
      items: [
        { title: "用户中心", url: "/", icon: IconGauge },
      ],
    },
    {
      label: "个人资料",
      items: [
        // 下游 API 支付接口关闭时隐藏入口（页面与逻辑保留，直访 URL 仍可用）
        { title: "API信息", url: "/security", icon: IconShieldLock, feature: "api_pay_enable" },
        { title: "修改资料", url: "/user-center", icon: IconUserEdit },
        { title: "修改密码", url: "/password", icon: IconLock },
      ],
    },
    {
      label: "交易中心",
      items: [
        { title: "订单记录", url: "/orders", icon: IconListDetails },
        { title: "结算记录", url: "/settle", icon: IconReceipt },
        { title: "资金明细", url: "/records", icon: IconHistory },
      ],
    },
    {
      label: "下级代理",
      feature: "agent_enable",
      items: [
        { title: "代理管理", url: "/agents", icon: IconUsers },
        { title: "代理业绩", url: "/agent-performance", icon: IconChartBar },
      ],
    },
    {
      label: "其他",
      items: [
        { title: "开发文档", url: "/docs/", icon: IconBook, external: true },
        { title: "产品QQ群", url: "", icon: IconBrandQq, external: true, urlFeature: "site_qq_group" },
        { title: "APP下载", url: "", icon: IconDeviceMobile, external: true, urlFeature: "site_app_url" },
      ],
    },
  ]
}

/** 按站点功能开关过滤菜单：单项隐藏、整组隐藏、外链取设置值，组内空则丢弃该组。
 *  admin/merchant 两端菜单结构一致，共用同一过滤器。 */
export function filterMenu(sections: MenuSection[], f: SiteFeatures): MenuSection[] {
  const visible = (item: NavItem): NavItem | null => {
    if (item.urlFeature) {
      const url = String(f[item.urlFeature] ?? "").trim()
      return url ? { ...item, url } : null
    }
    if (item.feature && f[item.feature] === false) return null
    return item
  }
  return sections
    .filter((s) => !s.feature || f[s.feature] !== false)
    .map((s) => ({ ...s, items: s.items.map(visible).filter((i): i is NavItem => i !== null) }))
    .filter((s) => s.items.length > 0)
}
