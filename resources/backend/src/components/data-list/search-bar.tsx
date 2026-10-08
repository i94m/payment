import type { ReactNode } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { IconSearch } from "@tabler/icons-react"

interface SearchBarProps {
  keyword?: string
  onKeywordChange?: (value: string) => void
  searchPlaceholder?: string
  filters?: ReactNode
  onReset?: () => void
  resetLabel?: string
  selectedCount?: number
  batchActions?: ReactNode
  primaryAction?: ReactNode
}

export function SearchBar({
  keyword,
  onKeywordChange,
  searchPlaceholder,
  filters,
  onReset,
  resetLabel = "Reset",
  batchActions,
  primaryAction,
}: SearchBarProps) {
  return (
    <div className="flex items-center gap-3">
      <div className="relative flex-1 max-w-sm">
        <IconSearch className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <Input
          placeholder={searchPlaceholder}
          className="pl-9"
          value={keyword ?? ""}
          onChange={(e) => onKeywordChange?.(e.target.value)}
        />
      </div>
      {onReset && (
        <Button variant="outline" onClick={onReset}>
          {resetLabel}
        </Button>
      )}
      {filters}
      <div className="flex-1" />
      {batchActions}
      {primaryAction}
    </div>
  )
}
