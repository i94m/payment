import * as React from "react"
import { useNavigate } from "react-router-dom"
import { IconBell } from "@tabler/icons-react"
import { messagesApi } from "@/api"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

// 后台通知铃铛（仅 admin 模块渲染）：60s 轮询未读数，点击进通知中心
export function NotificationBell() {
  const navigate = useNavigate()
  const [count, setCount] = React.useState(0)

  React.useEffect(() => {
    let stopped = false
    const load = () => messagesApi.unreadCount()
      .then((res) => { if (!stopped) setCount(res.count) })
      .catch(() => {})
    load()
    const timer = setInterval(load, 60_000)
    return () => { stopped = true; clearInterval(timer) }
  }, [])

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="ghost" size="icon" className="relative size-8"
          onClick={() => navigate("/system/notifications")}>
          <IconBell className="size-4" />
          {count > 0 && (
            <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium leading-none text-destructive-foreground">
              {count > 99 ? "99+" : count}
            </span>
          )}
        </Button>
      </TooltipTrigger>
      <TooltipContent>通知中心</TooltipContent>
    </Tooltip>
  )
}
