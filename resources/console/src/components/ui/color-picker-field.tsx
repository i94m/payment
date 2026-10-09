"use client"

import * as React from "react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

interface ColorPickerFieldProps {
  value?: string
  onChange?: (value: string) => void
  placeholder?: string
  className?: string
  disabled?: boolean
}

const presetColors = [
  "#ef4444", "#f97316", "#f59e0b", "#eab308", "#84cc16",
  "#22c55e", "#14b8a6", "#06b6d4", "#0ea5e9", "#3b82f6",
  "#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899",
  "#f43f5e", "#000000", "#374151", "#6b7280", "#9ca3af",
  "#d1d5db", "#e5e7eb", "#f3f4f6", "#f9fafb", "#ffffff",
]

function ColorPickerField({
  value,
  onChange,
  placeholder = "选择颜色",
  className,
  disabled,
}: ColorPickerFieldProps) {
  const [open, setOpen] = React.useState(false)
  const [inputVal, setInputVal] = React.useState(value ?? "")

  React.useEffect(() => {
    setInputVal(value ?? "")
  }, [value])

  const isValid = /^#[0-9a-fA-F]{6}$/.test(inputVal)

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            className="size-9 shrink-0 rounded-md border shadow-xs transition-colors hover:opacity-80 disabled:opacity-50"
            style={{ backgroundColor: isValid ? inputVal : "#ffffff" }}
          />
        </PopoverTrigger>
        <PopoverContent className="w-auto p-3" align="start">
          <div className="grid grid-cols-5 gap-1.5 mb-3">
            {presetColors.map((c) => (
              <button
                key={c}
                type="button"
                className={cn(
                  "size-7 rounded-md border transition-transform hover:scale-110",
                  value === c && "ring-2 ring-primary ring-offset-1"
                )}
                style={{ backgroundColor: c }}
                onClick={() => {
                  onChange?.(c)
                  setInputVal(c)
                  setOpen(false)
                }}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <input
              type="color"
              value={isValid ? inputVal : "#000000"}
              onChange={(e) => {
                const c = e.target.value
                onChange?.(c)
                setInputVal(c)
              }}
              className="size-8 cursor-pointer border-0 bg-transparent p-0"
            />
            <span className="text-xs text-muted-foreground">自定义</span>
          </div>
        </PopoverContent>
      </Popover>
      <Input
        value={inputVal}
        onChange={(e) => {
          setInputVal(e.target.value)
          if (/^#[0-9a-fA-F]{6}$/.test(e.target.value)) {
            onChange?.(e.target.value)
          }
        }}
        onBlur={() => {
          if (isValid) onChange?.(inputVal)
        }}
        placeholder={placeholder}
        disabled={disabled}
        className="flex-1"
      />
    </div>
  )
}

export { ColorPickerField }
