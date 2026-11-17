import * as React from "react"
import { cn } from "@/lib/utils"
import { payIconSrc } from "@/lib/pay-icons"

interface PayIconProps {
  /** payment_types.code（未配置 icon 时按 code 取内置图标） */
  code?: string | null
  /** payment_types.icon（后台配置的图标地址） */
  icon?: string | null
  className?: string
}

/** 支付方式图标：无图标/加载失败时什么都不渲染，调用方按纯文字展示即可 */
export function PayIcon({ code, icon, className }: PayIconProps) {
  const src = payIconSrc(code, icon)
  if (!src) return null
  return (
    <img
      src={src}
      alt=""
      aria-hidden
      loading="lazy"
      className={cn("h-[18px] w-[18px] shrink-0 rounded-[3px]", className)}
      onError={(e) => { e.currentTarget.style.display = "none" }}
    />
  )
}

/** 图标 + 名称：支付方式在列表/详情里的统一展示 */
export function PayTypeLabel({
  code,
  icon,
  name,
  className,
}: PayIconProps & { name?: string | null }) {
  const label = name || code || ""
  return (
    <span className={cn("flex items-center gap-1.5", className)}>
      <PayIcon code={code} icon={icon} />
      <span>{label}</span>
    </span>
  )
}

export { PayIcon as default }
