import * as React from "react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { IconMinus, IconPlus } from "@tabler/icons-react"

interface NumberInputProps {
  value?: number
  onChange?: (value: number | undefined) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  placeholder?: string
  className?: string
}

function roundToStep(val: number, step: number): number {
  const decimals = String(step).includes(".") ? String(step).split(".")[1].length : 0
  const factor = Math.pow(10, decimals)
  return Math.round(val * factor) / factor
}

function clamp(val: number, min?: number, max?: number): number {
  if (min !== undefined && val < min) return min
  if (max !== undefined && val > max) return max
  return val
}

function NumberInput({
  value,
  onChange,
  min,
  max,
  step = 1,
  disabled,
  placeholder,
  className,
}: NumberInputProps) {
  const increment = () => {
    const next = clamp(roundToStep((value ?? 0) + step, step), min, max)
    onChange?.(next)
  }

  const decrement = () => {
    const next = clamp(roundToStep((value ?? 0) - step, step), min, max)
    onChange?.(next)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowUp") { e.preventDefault(); increment() }
    if (e.key === "ArrowDown") { e.preventDefault(); decrement() }
  }

  const handleBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    const raw = e.target.value
    if (raw === "") { onChange?.(undefined); return }
    const parsed = parseFloat(raw)
    if (!isNaN(parsed)) {
      onChange?.(clamp(parsed, min, max))
    }
  }

  return (
    <div className={cn("flex items-center", className)}>
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="size-9 rounded-r-none"
        onClick={decrement}
        disabled={disabled || (min !== undefined && value !== undefined && value <= min)}
      >
        <IconMinus className="size-3.5" />
      </Button>
      <Input
        type="number"
        value={value ?? ""}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === "") { onChange?.(undefined); return }
          const parsed = parseFloat(raw)
          if (!isNaN(parsed)) onChange?.(parsed)
        }}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder={placeholder}
        className="h-9 w-20 rounded-none border-x-0 text-center [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
      />
      <Button
        type="button"
        variant="outline"
        size="icon"
        className="size-9 rounded-l-none"
        onClick={increment}
        disabled={disabled || (max !== undefined && value !== undefined && value >= max)}
      >
        <IconPlus className="size-3.5" />
      </Button>
    </div>
  )
}

export { NumberInput }
