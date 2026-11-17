import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar"
import { AppSidebar } from "@/components/layout/app-sidebar"
import { SiteHeader } from "@/components/layout/site-header"
import { TopLayout } from "@/components/layout/top-layout"
import { useLayoutStore } from "@/stores/layout-store"
import { useLocation } from "react-router-dom"
import type { ReactNode } from "react"

const sidebarStyle = {
  "--sidebar-width": "calc(var(--spacing) * 56)",
  "--header-height": "calc(var(--spacing) * 12)",
} as React.CSSProperties

export function AppLayout({ children }: { children: ReactNode }) {
  // 用户偏好：左侧侧栏（默认）/ 顶部导航；切换在用户下拉菜单里
  const navPosition = useLayoutStore((s) => s.navPosition)

  if (navPosition === "top") {
    return <TopLayout>{children}</TopLayout>
  }

  return <SidebarLayout>{children}</SidebarLayout>
}

function SidebarLayout({ children }: { children: ReactNode }) {
  const location = useLocation()

  return (
    <SidebarProvider style={sidebarStyle}>
      <AppSidebar />
      <SidebarInset>
        <SiteHeader />
        <div className="flex-1 overflow-auto @container/main">
          <div
            key={location.pathname}
            className="animate-page-slide-in"
          >
            {children}
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
