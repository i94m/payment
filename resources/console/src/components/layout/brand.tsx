import { Link } from "react-router-dom"
import { useSiteStore } from "@/stores/site-store"
import { APP_MODULE } from "@/lib/config"
import { cn } from "@/lib/utils"

interface BrandProps {
  /** 侧边栏内使用：折叠成图标时隐藏文字 */
  inSidebar?: boolean
  /** 移动端抽屉里点击后关闭 */
  onNavigate?: () => void
  className?: string
}

/** 站点品牌块：logo + 站点名（admin 额外显示镜像版本），侧栏/顶栏共用 */
export function Brand({ inSidebar = false, onNavigate, className }: BrandProps) {
  const siteName = useSiteStore((s) => s.siteName)
  const version = useSiteStore((s) => s.version)
  const revision = useSiteStore((s) => s.revision)

  return (
    <Link to="/" onClick={onNavigate} className={cn("flex min-w-0 items-center gap-2", className)}>
      <img src="/images/logo.svg" alt="" className="size-5! shrink-0" />
      <span className={cn("flex min-w-0 items-baseline gap-1.5 text-sm leading-tight", inSidebar && "group-data-[collapsible=icon]:hidden")}>
        <span className="truncate font-medium">{siteName}</span>
        {/* 版本号仅管理后台展示（商户/代理端不暴露平台版本）：
            取自镜像构建期，源码运行为 dev，hover 可看构建提交号；与站点名同一行 */}
        {APP_MODULE === "admin" && version && (
          <span
            className={cn(
              "shrink-0 text-[11px] leading-none",
              inSidebar ? "text-sidebar-foreground/50" : "text-muted-foreground/70",
            )}
            title={revision ? `构建提交 ${revision}` : undefined}
          >
            {/^\d/.test(version) ? `v${version}` : version}
          </span>
        )}
      </span>
    </Link>
  )
}
