import type * as React from "react"
import type { Table } from "@tanstack/react-table"
import { flexRender } from "@tanstack/react-table"
import { cn } from "@/lib/utils"
import { IconInbox, IconArrowsUpDown, IconArrowUp, IconArrowDown } from "@tabler/icons-react"
import { Empty, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table as UiTable,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> {
    headClassName?: string
    cellClassName?: string
    headStyle?: React.CSSProperties
    sticky?: "left" | "right"
  }
}

interface DataTableProps<TData> {
  table: Table<TData>
  loading?: boolean
  emptyMessage?: string
  /** 按行数据追加样式（如扣量单底色），返回 undefined 表示不额外加类 */
  rowClassName?: (row: TData) => string | undefined
  children?: React.ReactNode
}

function stickyClass(sticky: "left" | "right" | undefined, zone: "head" | "body"): string {
  if (sticky === "left") return `sticky left-0 z-10 ${zone === "head" ? "bg-muted" : "bg-card"}`
  if (sticky === "right") return `sticky right-0 z-10 ${zone === "head" ? "bg-muted" : "bg-card"}`
  return ""
}

export function DataTable<TData>({
  table,
  loading = false,
  emptyMessage = "暂无数据",
  rowClassName,
  children,
}: DataTableProps<TData>) {
  const colCount = table.getHeaderGroups()[0]?.headers.length ?? 1

  return (
    <div data-slot="data-table" className="overflow-hidden bg-card text-card-foreground">
      <UiTable>
      <TableHeader>
        {table.getHeaderGroups().map((headerGroup) => (
          <TableRow key={headerGroup.id} className="hover:bg-transparent">
            {headerGroup.headers.map((header) => {
              const sticky = header.column.columnDef.meta?.sticky
              const sortable = header.column.getCanSort()
              const sortDir = header.column.getIsSorted()
              return (
                <TableHead
                  key={header.id}
                  className={cn(
                    header.column.columnDef.meta?.headClassName,
                    stickyClass(sticky, "head"),
                    sortable && "cursor-pointer select-none hover:bg-foreground/5",
                  )}
                  style={header.column.columnDef.meta?.headStyle}
                  onClick={sortable ? header.column.getToggleSortingHandler() : undefined}
                >
                  <span className="inline-flex items-center gap-1">
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                    {sortable && (
                      sortDir === "asc" ? <IconArrowUp className="size-3.5" />
                      : sortDir === "desc" ? <IconArrowDown className="size-3.5" />
                      : <IconArrowsUpDown className="size-3.5 opacity-40" />
                    )}
                  </span>
                </TableHead>
              )
            })}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {loading ? (
          Array.from({ length: 8 }).map((_, rowIndex) => (
            <TableRow key={`skeleton-row-${rowIndex}`} className="hover:bg-transparent">
              {table.getHeaderGroups()[0]?.headers.map((header) => (
                <TableCell key={header.id} className="py-3">
                  <Skeleton className="h-4 w-full max-w-32" />
                </TableCell>
              ))}
            </TableRow>
          ))
        ) : table.getRowModel().rows.length === 0 ? (
          <TableRow>
            <TableCell colSpan={colCount} className="h-48 text-center">
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <IconInbox />
                  </EmptyMedia>
                  <EmptyTitle>{emptyMessage}</EmptyTitle>
                </EmptyHeader>
              </Empty>
            </TableCell>
          </TableRow>
        ) : (
          table.getRowModel().rows.map((row) => (
            <TableRow
              key={row.id}
              data-state={row.getIsSelected() ? "selected" : undefined}
              className={rowClassName?.(row.original)}
            >
              {row.getVisibleCells().map((cell) => {
                const sticky = cell.column.columnDef.meta?.sticky
                return (
                  <TableCell
                    key={cell.id}
                    className={cn(cell.column.columnDef.meta?.cellClassName, stickyClass(sticky, "body"))}
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                )
              })}
            </TableRow>
          ))
        )}
      </TableBody>
      </UiTable>
      {children}
    </div>
  )
}
