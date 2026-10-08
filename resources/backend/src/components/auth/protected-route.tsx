import { Navigate, useLocation } from "react-router-dom"
import { useAuthStore } from "@/stores/auth-store"
import type { ReactNode } from "react"
import { Error403 } from "@/pages/errors/403"

export function ProtectedRoute({ children, permission }: { children: ReactNode; permission?: string }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const location = useLocation()

  if (!isAuthenticated) {
    // 必须用绝对路径 /login:相对 "login" 会在嵌套路由(如 /examples/list)上被解析为
    // 追加,导致 /examples/list/login/login/login... 无限累加。
    // from 排除含 /login 的脏路径,避免登录后又被带回污染 URL。
    const from = location.pathname.includes("/login") ? "/" : location.pathname
    return <Navigate to="/login" state={{ from }} replace />
  }

  // "*" 为全量放行（商户端单一角色由后端下发 permissions=["*"]）
  if (permission && !user?.is_super_admin && !permissions.includes("*") && !permissions.includes(permission)) {
    return <Error403 />
  }

  return <>{children}</>
}
