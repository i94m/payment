import type { ReactNode } from "react"
import { useLocation } from "react-router-dom"
import { TopNav } from "@/components/layout/top-nav"
import { TOP_LAYOUT_MAX_WIDTH } from "@/lib/layout"
import { cn } from "@/lib/utils"

/** 顶栏布局：水平导航 + 内容区（保留 @container/main 容器查询）；顶部模式不展示面包屑 */
export function TopLayout({ children }: { children: ReactNode }) {
  const location = useLocation()

  return (
    <div className="flex min-h-svh w-full flex-col bg-background">
      <TopNav />
      <main className="relative flex min-h-0 w-full flex-1 flex-col overflow-hidden bg-background">
        <div className="flex-1 overflow-auto">
          <div className={cn("mx-auto w-full @container/main", TOP_LAYOUT_MAX_WIDTH)}>
            <div key={location.pathname} className="animate-page-slide-in">
              {children}
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
