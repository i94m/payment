import { Navigate, useLocation } from "react-router-dom"
import { useAuthStore } from "@/stores/auth-store"
import type { ReactNode } from "react"
import { Error403 } from "@/pages/errors/403"

/** 登录后要回到的地址：必须用绝对路径(相对 "login" 会在嵌套路由上被解析成追加)；
 *  from 排除含 /login 的脏路径，避免登录后又被带回污染 URL。 */
function useFromPath() {
  const location = useLocation()
  return location.pathname.includes("/login") ? "/" : location.pathname
}

/**
 * 登录守卫：包在后台布局(侧栏 / 顶栏 / 内容区)的最外层。
 *
 * 未登录时整个后台框架都不挂载，直接跳登录页。若只在各页面路由上拦截，
 * 作为父路由的布局会先渲染一帧、再由 Navigate 顶掉，表现为「后台页面一闪而过」。
 * 登录 / SSO / 注册等公开路由不经过这里，保持免登录可达。
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const from = useFromPath()

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from }} replace />
  }

  return <>{children}</>
}

export function ProtectedRoute({ children, permission, superOnly }: {
  children: ReactNode
  permission?: string
  /** 仅超管可进：后端对应模块的接口全是 super-only，子账号进来只会 403 */
  superOnly?: boolean
}) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const from = useFromPath()

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from }} replace />
  }

  if (superOnly && !user?.is_super_admin) {
    return <Error403 />
  }

  // "*" 为全量放行（商户端单一角色由后端下发 permissions=["*"]）
  if (permission && !user?.is_super_admin && !permissions.includes("*") && !permissions.includes(permission)) {
    return <Error403 />
  }

  return <>{children}</>
}
