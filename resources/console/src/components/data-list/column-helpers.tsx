import * as React from "react"
import type { ColumnDef } from "@tanstack/react-table"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import { Switch } from "@/components/ui/switch"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"
import {
  IconCircleCheckFilled, IconCircleXFilled, IconCopy, IconCheck, IconDots,
} from "@tabler/icons-react"
import type { ActionItem, StatusBadgeOption } from "./types"

export function selectColumn<T>(): ColumnDef<T> {
  return {
    id: "select",
    enableSorting: false,
    header: ({ table }) => (
      <Checkbox
        checked={table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && "indeterminate")}
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
      />
    ),
    cell: ({ row }) => (
      <Checkbox
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
      />
    ),
    meta: { headClassName: "w-10", cellClassName: "", sticky: "left" },
  }
}

export function actionsColumn<T>(
  actions: ActionItem<T>[],
  opts?: { header?: string; width?: number; sticky?: boolean },
): ColumnDef<T> {
  const width = opts?.width
  const inlineActions = actions.filter((a) => a.placement !== "menu")
  const menuActions = actions.filter((a) => a.placement === "menu")
  return {
    id: "actions",
    enableSorting: false,
    header: () => opts?.header ?? "操作",
    cell: ({ row }) => (
      <div
        className="flex max-w-[430px] flex-wrap items-center justify-center gap-1"
        style={width ? { maxWidth: width } : undefined}
      >
        {inlineActions.map((action, i) => (
          <Button
            key={i}
            variant="ghost"
            className={`h-7 gap-1 px-2 text-xs ${
              action.variant === "destructive"
                ? "text-destructive hover:text-destructive"
                : "text-muted-foreground"
            }`}
            title={action.title ?? action.label}
            onClick={() => action.onClick(row.original)}
          >
            {action.icon}
            {action.label}
          </Button>
        ))}
        {menuActions.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger
              render={
                <Button
                  variant="ghost"
                  className="h-7 w-7 p-0 text-muted-foreground"
                  title={"更多操作"}
                />
              }
            >
              <IconDots className="size-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-36">
              {menuActions.map((action, i) => (
                <DropdownMenuItem
                  key={i}
                  variant={action.variant === "destructive" ? "destructive" : "default"}
                  onClick={() => action.onClick(row.original)}
                >
                  {action.icon}
                  {action.label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    ),
    // width: 操作列固定宽度（w=minWidth，防被自动表格布局压缩成多行），
    // 不传时保持默认 w-[72px] 内容自适应
    // sticky: 默认贴右；最后一列本身就是关键内容（如平台订单号在操作列左侧、
    // 表格略微超宽）时关掉，避免操作列把最后一列盖住
    meta: {
      headClassName: width ? "text-center" : "w-[72px] text-center",
      headStyle: width ? { width, minWidth: width } : undefined,
      cellClassName: "text-center",
      sticky: opts?.sticky === false ? undefined : "right",
    },
  }
}

export function highlightCell(text: string, keyword: string | string[]): React.ReactNode {
  if (!keyword || !text) return text
  const keywords = Array.isArray(keyword) ? keyword.filter(Boolean) : [keyword]
  if (keywords.length === 0) return text

  const lower = text.toLowerCase()
  const marks: { start: number; end: number }[] = []
  for (const kw of keywords) {
    const lkw = kw.toLowerCase()
    let pos = 0
    while ((pos = lower.indexOf(lkw, pos)) !== -1) {
      marks.push({ start: pos, end: pos + lkw.length })
      pos += lkw.length
    }
  }
  if (marks.length === 0) return text

  marks.sort((a, b) => a.start - b.start)
  const merged: { start: number; end: number }[] = [marks[0]]
  for (let i = 1; i < marks.length; i++) {
    const last = merged[merged.length - 1]
    if (marks[i].start <= last.end) {
      last.end = Math.max(last.end, marks[i].end)
    } else {
      merged.push(marks[i])
    }
  }

  const parts: React.ReactNode[] = []
  let cursor = 0
  for (let i = 0; i < merged.length; i++) {
    const { start, end } = merged[i]
    if (cursor < start) parts.push(text.slice(cursor, start))
    parts.push(
      <mark key={i} className="rounded px-0.5" style={{ backgroundColor: "#fef08a", color: "#dc2626" }}>
        {text.slice(start, end)}
      </mark>
    )
    cursor = end
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return <>{parts}</>
}

export function switchCell(checked: boolean, onChange: () => void): React.ReactNode {
  return <Switch checked={checked} onCheckedChange={onChange} />
}

export function dateCell(value: string | null, fallback?: string): React.ReactNode {
  const display = value || fallback || ""
  return <span className="text-sm text-muted-foreground">{display}</span>
}

export function secondaryCell(value: string | number): React.ReactNode {
  return <span className="text-sm text-muted-foreground">{value}</span>
}

export function statusBadgeCell(value: unknown, options: StatusBadgeOption[]): React.ReactNode {
  const match = options.find((o) => o.value === value)
  if (!match) return <Badge variant="outline">{String(value)}</Badge>
  return <Badge variant={match.variant ?? "default"}>{match.label}</Badge>
}

export function yesNoCell(value: boolean): React.ReactNode {
  if (value) {
    return <IconCircleCheckFilled className="size-5 text-green-500" />
  }
  return <IconCircleXFilled className="size-5 text-red-400" />
}

export function booleanDotCell(value: boolean): React.ReactNode {
  return (
    <span className="flex items-center justify-center">
      <span className={`size-2 rounded-full ${value ? "bg-green-500" : "bg-muted-foreground/30"}`} />
    </span>
  )
}

export function copyableCell(text: string, maxWidth = 100): React.ReactNode {
  return <CopyableCell text={text} maxWidth={maxWidth} />
}

function CopyableCell({ text, maxWidth }: { text: string; maxWidth: number }) {
  const { copyToClipboard, isCopied } = useCopyToClipboard()

  return (
    <div className="flex items-center gap-1">
      <span className="text-sm truncate" style={{ maxWidth }} title={text}>{text}</span>
      <Button variant="ghost" size="icon" className="size-5 shrink-0" onClick={() => copyToClipboard(text)}>
        {isCopied
          ? <IconCheck className="size-3 text-green-500" />
          : <IconCopy className="size-3 text-muted-foreground" />}
      </Button>
    </div>
  )
}

export function truncatedCell(text: string, maxWidth = 100): React.ReactNode {
  return (
    <span
      className="block text-sm truncate"
      style={{ maxWidth }}
      title={text}
    >
      {text}
    </span>
  )
}
