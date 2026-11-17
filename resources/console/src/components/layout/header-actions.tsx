import * as React from "react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { IconArrowsMaximize, IconArrowsMinimize } from "@tabler/icons-react"
import { APP_MODULE } from "@/lib/config"
import { NotificationBell } from "@/components/layout/notification-bell"
import { ThemeToggle } from "@/components/layout/theme-toggle"
import { FEATURE_NOTIFICATION_CENTER } from "@/lib/features"

/** 头部右侧动作：通知中心 + 明暗模式 + 全屏，侧栏头部与顶栏共用 */
export function HeaderActions() {
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

  return (
    <div className="flex items-center gap-0.5 sm:gap-1">
      {/* 通知中心铃铛：入口由 FEATURE_NOTIFICATION_CENTER 控制，组件代码保留 */}
      {FEATURE_NOTIFICATION_CENTER && APP_MODULE === "admin" && <NotificationBell />}
      <ThemeToggle />
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8 hidden md:inline-flex" onClick={toggleFullscreen}>
            {isFullscreen ? <IconArrowsMinimize className="size-4" /> : <IconArrowsMaximize className="size-4" />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{isFullscreen ? "退出全屏" : "全屏"}</TooltipContent>
      </Tooltip>
    </div>
  )
}
