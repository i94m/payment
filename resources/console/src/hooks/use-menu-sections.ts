import * as React from "react"
import { SidebarContext } from "@/App"
import { useAuthStore } from "@/stores/auth-store"
import { useSiteStore } from "@/stores/site-store"
import type { MenuSection } from "@/apps/merchant/sidebar"

/**
 * 过滤后的侧边栏/顶栏菜单：权限 + 静态 hidden + 站点功能开关。
 *
 * 侧边栏（AppSidebar）与顶部导航（TopNav）共用同一份结果，保证两种布局
 * 的可见性完全一致。功能开关最终由 getMenu() 内部的 filterMenu 过滤，
 * 这里订阅 features 只是为了开关变化后触发重算（main.tsx 的 getMenu 读取
 * 的是 store 快照）。
 */
export function useMenuSections(): MenuSection[] {
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const features = useSiteStore((s) => s.features)
  const { getMenu } = React.useContext(SidebarContext)

  return React.useMemo(() => {
    const hasPermission = (key?: string) => {
      if (!key) return true
      if (user?.is_super_admin) return true
      // "*" = 全量放行（商户端单一角色由后端下发 permissions=["*"]）
      if (permissions.includes("*")) return true
      return permissions.includes(key)
    }

    return getMenu()
      .map((section) => ({
        ...section,
        // hidden 为静态隐藏（功能开关关闭），权限过滤之外再剔除一次
        items: section.items.filter((item) => !item.hidden && hasPermission(item.permission)),
      }))
      // 整组都被过滤掉时不渲染分组标题/下拉触发器
      .filter((section) => section.items.length > 0)
    // features 仅用于触发重算：getMenu 内部读取的是 store 快照，不直接引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [getMenu, user, permissions, features])
}
