import { APP_MODULE } from "@/lib/config"
import { getAdminMenu } from "@/apps/admin/sidebar"
import { getAgentMenu } from "@/apps/agent/sidebar"
import { getMerchantMenu, type MenuSection } from "@/apps/merchant/sidebar"

export interface PageMeta {
  /** 页面标题（面包屑末级 / 页签名） */
  title: string
  /** 侧边栏分组名（面包屑父级，如「交易管理」） */
  parents: string[]
}

/**
 * 分组菜单 → 页面元信息：sectionAsParent 为 true 时分组名进面包屑父级
 * （admin 沿用原折叠父项语义；merchant 侧边栏虽是平铺分区，分组名同样
 * 是页面的上级层级，进面包屑父级）。agent 菜单单组无标签，不受影响。
 * 入参为全量菜单（不做开关过滤）：被隐藏页面的直链访问/页签恢复仍需标题。
 * 消费方（面包屑/页签）只用 pathname 查表，带 query 的 url（/settle?pane=x）
 * 剥离 query 后按 first-wins 注册（/settle 由「结算记录」先占）。
 */
function flatten(menu: MenuSection[], sectionAsParent = false): Record<string, PageMeta> {
  const map: Record<string, PageMeta> = {}
  const register = (url: string, meta: PageMeta) => {
    const key = url.split("?")[0]
    if (!map[key]) map[key] = meta
  }
  for (const section of menu) {
    const parents = sectionAsParent && section.label ? [section.label] : []
    for (const item of section.items) {
      if (item.external) continue // 外链无页面
      register(item.url, { title: item.title, parents })
    }
  }
  return map
}

// 侧边栏菜单是页面标题/层级的唯一事实来源：面包屑与页签标题均由此推导，
// 避免手维护映射漂移（admin/merchant 同路径不同名也天然区分）。
// 菜单外页面（无侧边栏入口，仅直链/页内跳转访问）按模块手动补注册：
// 放在 flatten 之后覆盖同路径注册，保证这些页面也有面包屑/页签标题。
const EXTRA_META: Record<string, PageMeta> =
  APP_MODULE === "admin"
    ? {
        // 菜单无「修改资料」项，/user-center 页签标题用「账号信息」
        "/user-center": { title: "账号信息", parents: [] },
        "/pay/types": { title: "支付方式", parents: ["系统配置"] },
        "/system/accounts": { title: "管理员账号", parents: [] },
      }
    : APP_MODULE === "merchant"
      ? {}
      : {}

export const PAGE_META: Record<string, PageMeta> = {
  ...(APP_MODULE === "admin"
    ? flatten(getAdminMenu(), true)
    : APP_MODULE === "agent"
      ? flatten(getAgentMenu())
      : flatten(getMerchantMenu(), true)),
  ...EXTRA_META,
}
