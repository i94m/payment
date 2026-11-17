import { useAuthStore } from "@/stores/auth-store"

/**
 * 细粒度权限判断（用于动作按钮等前端显隐）。
 *
 * 与 ProtectedRoute / AppSidebar 同一口径：超管与后端下发的 "*" 全量放行，
 * 其余按后端返回的 permission key 精确匹配。前端隐藏只是体验优化，
 * 接口侧仍由 PermissionChecker 兜底 403。
 */
export function usePermission() {
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)

  return (key?: string) => {
    if (!key) return true
    if (user?.is_super_admin) return true
    return permissions.includes("*") || permissions.includes(key)
  }
}
