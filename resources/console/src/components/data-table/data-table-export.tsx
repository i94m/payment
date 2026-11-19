"use client"

import * as React from "react"
import { Button } from "@/components/ui/button"
import { IconDownload } from "@tabler/icons-react"
import { exportToExcel, type ExportColumn } from "@/lib/export"
import { EXPORT_ENABLED } from "@/lib/flags"
import type { Table } from "@tanstack/react-table"

interface DataTableExportProps<TData> {
  table: Table<TData>
  columns: ExportColumn[]
  filename?: string
  exportApi?: () => Promise<void>
}

export function DataTableExport<TData>({ table, columns, filename = "export", exportApi }: DataTableExportProps<TData>) {
  const [loading, setLoading] = React.useState(false)

  const handleExport = async () => {
    if (exportApi) {
      setLoading(true)
      try {
        await exportApi()
      } catch {
        // ignore
      } finally {
        setLoading(false)
      }
      return
    }

    const rows = table.getFilteredRowModel().rows
    const data = rows.map((row) => {
      const obj: Record<string, unknown> = {}
      for (const col of columns) {
        obj[col.key] = row.getValue(col.key)
      }
      return obj
    })
    await exportToExcel(data, filename, columns)
  }

  // 导出已在界面上整体隐藏（见 lib/flags.ts），恢复时改开关即可
  if (!EXPORT_ENABLED) return null

  return (
    <Button variant="outline" size="sm" onClick={handleExport} disabled={loading}>
      <IconDownload className="size-4" />
      {loading ? "加载中..." : "导出"}
    </Button>
  )
}
