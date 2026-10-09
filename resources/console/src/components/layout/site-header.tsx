import * as React from "react"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb"
import { IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react"
import { useLocation } from "react-router-dom"
import { PAGE_META } from "@/lib/page-meta"
import { APP_MODULE } from "@/lib/config"
import { NotificationBell } from "@/components/layout/notification-bell"

export function SiteHeader() {
  const location = useLocation()
  const [isFullscreen, setIsFullscreen] = React.useState(false)

  const toggleFullscreen = React.useCallback(() => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen()
    } else {
      document.exitFullscreen()
    }
  }, [])

  React.useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement)
    document.addEventListener("fullscreenchange", handler)
    return () => document.removeEventListener("fullscreenchange", handler)
  }, [])

  // 面包屑按菜单层级推导：分组名 → 页面名（见 lib/page-meta）
  const def = PAGE_META[location.pathname]
  const lastSegment = location.pathname.split("/").filter(Boolean).pop() ?? ""
  const crumbs: { path: string; label: string; isLast: boolean }[] = def
    ? [
        ...def.parents.map((label) => ({ path: "#", label, isLast: false })),
        { path: location.pathname, label: def.title, isLast: true },
      ]
    // 未登记页面（403/500 等）：退化显示末段路径
    : [{ path: location.pathname, label: decodeURIComponent(lastSegment) || "仪表盘", isLast: true }]

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
      <div className="flex w-full items-center gap-1 px-3 sm:px-4 lg:gap-2 lg:px-6">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mx-1.5 sm:mx-2 h-4" />
        <Breadcrumb>
          <BreadcrumbList>
            {crumbs.map((crumb, idx) => (
              <React.Fragment key={crumb.path}>
                {idx > 0 && <BreadcrumbSeparator />}
                <BreadcrumbItem>
                  {crumb.isLast ? (
                    <BreadcrumbPage className="text-sm font-medium text-foreground">{crumb.label}</BreadcrumbPage>
                  ) : (
                    <span className="text-sm text-muted-foreground">{crumb.label}</span>
                  )}
                </BreadcrumbItem>
              </React.Fragment>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
        <div className="ml-auto flex items-center gap-0.5 sm:gap-1">
          {APP_MODULE === "admin" && <NotificationBell />}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" className="size-8 hidden md:inline-flex" onClick={toggleFullscreen}>
                {isFullscreen ? <IconArrowsMinimize className="size-4" /> : <IconArrowsMaximize className="size-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{isFullscreen ? "退出全屏" : "全屏"}</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </header>
  )
}
