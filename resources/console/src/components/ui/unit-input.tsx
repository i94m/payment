import * as React from "react"
import { cn } from "@/lib/utils"
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group"

type UnitInputProps = Omit<React.ComponentProps<typeof InputGroupInput>, "className"> & {
  /** 显示在输入框内的单位（元 / % / 分钟…），值仍只由 input 承载 */
  unit: React.ReactNode
  /** 外层 InputGroup 的样式（宽度 / 高度等） */
  className?: string
  /** 输入框自身的样式（如 text-right、字号） */
  inputClassName?: string
  /** 单位位置，默认后缀 */
  align?: "inline-start" | "inline-end"
  addonClassName?: string
}

/**
 * 带单位的输入框：单位用 InputGroup 前后缀展示，避免把「（元）」「（%）」写进标签。
 */
function UnitInput({
  unit,
  className,
  inputClassName,
  align = "inline-end",
  addonClassName,
  ...props
}: UnitInputProps) {
  const addon = (
    <InputGroupAddon align={align} className={addonClassName}>
      {unit}
    </InputGroupAddon>
  )

  return (
    <InputGroup
      className={className}
      data-disabled={props.disabled ? "true" : undefined}
    >
      {align === "inline-start" && addon}
      <InputGroupInput {...props} className={cn(inputClassName)} />
      {align === "inline-end" && addon}
    </InputGroup>
  )
}

export { UnitInput }
