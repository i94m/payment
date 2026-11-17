import { HashRouter, Outlet, useRoutes, useLocation } from "react-router-dom"
import { useEffect } from "react"
import { TooltipProvider } from "@/components/ui/tooltip"
import { Toaster } from "@/components/ui/sonner"
import { AppLayout } from "@/components/layout/app-layout"
import { ProtectedRoute } from "@/components/auth/protected-route"
import { useAuthStore } from "@/stores/auth-store"
import { useSiteStore } from "@/stores/site-store"
import { APP_MODULE } from "@/lib/config"
import { LoginPage } from "@/pages/login"
import { SsoLoginPage } from "@/pages/sso-login"
import { RegisterPage } from "@/pages/register"
import { Error404 } from "@/pages/errors/404"
import { ImagePreviewPortal } from "@/components/ui/image-viewer"
import { ErrorBoundary } from "@/components/error-boundary"
import { loadStorageConfig } from "@/lib/storage"
import type { MenuSection } from "@/apps/merchant/sidebar"
import type { PermissionRouteObject } from "@/apps/admin/routes"
import NProgress from "nprogress"
import "nprogress/nprogress.css"

import * as React from "react"

interface AppProps {
  routes: PermissionRouteObject[]
  // 两侧均为分组平铺菜单（admin/merchant 结构一致）
  getMenu: () => MenuSection[]
}

const SidebarContext = React.createContext<{
  getMenu: () => MenuSection[]
}>({ getMenu: () => [] })

export { SidebarContext }

function LayoutShell({ children }: { children: React.ReactNode }) {
  useNProgress()
  return (
    // Suspense 包裹内容区：lazy 路由加载 chunk 时仅内容区 fallback，布局(侧边栏/顶栏)保留
    <AppLayout><React.Suspense fallback={null}>{children}</React.Suspense></AppLayout>
  )
}

function useNProgress() {
  const location = useLocation()
  const prevPathname = React.useRef(location.pathname)

  useEffect(() => {
    if (location.pathname !== prevPathname.current) {
      prevPathname.current = location.pathname
      NProgress.start()
      const raf = requestAnimationFrame(() => {
        NProgress.done()
      })
      return () => cancelAnimationFrame(raf)
    }
  }, [location.pathname])
}

function AppRoutes({ routes }: { routes: PermissionRouteObject[] }) {
  const wrappedRoutes = routes.map((route) => ({
    ...route,
    element: (
      <ProtectedRoute permission={route.permission}>
        {route.element}
      </ProtectedRoute>
    ),
  }))

  const element = useRoutes([
    { path: "login", element: <LoginPage /> },
    // SSO 落地页：公开路由（一次性 token 自证身份），免登录守卫
    { path: "sso-login", element: <SsoLoginPage /> },
    // 商户自助注册：公开路由，仅商户端挂载（admin 端落到 404）
    { path: "register", element: APP_MODULE === "merchant" ? <RegisterPage /> : <Error404 /> },
    {
      element: <LayoutShell><Outlet /></LayoutShell>,
      children: wrappedRoutes,
    },
  ])
  return element
}

export default function App({ routes, getMenu }: AppProps) {
  const token = useAuthStore((s) => s.token)
  const user = useAuthStore((s) => s.user)
  const fetchProfile = useAuthStore((s) => s.fetchProfile)
  const siteName = useSiteStore((s) => s.siteName)
  const fetchSite = useSiteStore((s) => s.fetchSite)

  // 仅用于「刷新页面后从持久化 token 恢复 profile」:登录流程由 login()
  // 内部自行调用 fetchProfile(),故此处只在首次挂载执行一次,避免重复请求。
  useEffect(() => {
    if (token && !user) {
      fetchProfile().catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 存储配置跟随后端 STORAGE_PROVIDER：云存储时把文件访问域名切到 bucket 域名。
  // 依赖 user 而非 token：避免用「已过期但尚未校验」的 token 去打接口触发 401 登出。
  useEffect(() => {
    if (!user) return
    void loadStorageConfig()
  }, [user])

  // 站点名称（后台可改）：拉取一次并同步浏览器标签页标题
  useEffect(() => {
    fetchSite()
  }, [fetchSite])

  useEffect(() => {
    const moduleTitle = APP_MODULE === "admin" ? "管理后台" : APP_MODULE === "agent" ? "代理中心" : "商户中心"
    document.title = siteName ? `${siteName} ${moduleTitle}` : moduleTitle
  }, [siteName])

  return (
    <ErrorBoundary>
      <SidebarContext.Provider value={{ getMenu }}>
        <TooltipProvider>
          <HashRouter>
            <AppRoutes routes={routes} />
            <Toaster />
            <ImagePreviewPortal />
          </HashRouter>
        </TooltipProvider>
      </SidebarContext.Provider>
    </ErrorBoundary>
  )
}
