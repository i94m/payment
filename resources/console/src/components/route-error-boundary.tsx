import type { ReactNode } from "react"
import { useLocation } from "react-router-dom"
import { ErrorBoundary } from "@/components/error-boundary"

/**
 * 路由级错误边界：单个页面渲染崩溃（含 lazy chunk 加载失败）只替换内容区，
 * 侧边栏与顶栏保留；切换路由后自动复位，不需要刷新整站。
 */
export function RouteErrorBoundary({ children }: { children?: ReactNode }) {
  const location = useLocation()
  return <ErrorBoundary resetKey={location.pathname}>{children}</ErrorBoundary>
}
