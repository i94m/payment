import type * as React from "react"

import { cn } from "@/lib/utils"

/**
 * 列表页筛选区容器：无卡片表面/阴影/圆角/边框，
 * 仅保留页面左右留白，与下方 DataTable 卡片上下呼应，内部为纵向 gap-3 排布。
 */
function FilterCard({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("mx-4 flex flex-col gap-3 lg:mx-6", className)} {...props} />
  )
}

export { FilterCard }
