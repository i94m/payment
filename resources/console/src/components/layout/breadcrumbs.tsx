import * as React from "react"
import { useLocation } from "react-router-dom"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { PAGE_META } from "@/lib/page-meta"

/** 面包屑按菜单层级推导：分组名 → 页面名（见 lib/page-meta） */
export function Breadcrumbs() {
  const location = useLocation()

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
  )
}
