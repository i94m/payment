export interface ExportColumn {
  key: string
  title: string
}

// xlsx 体积大（~400KB），仅导出 Excel 时动态加载，不进首屏主包
type XLSXModule = typeof import("xlsx")

export async function exportToExcel(
  data: Record<string, unknown>[],
  filename: string,
  columns: ExportColumn[]
) {
  const XLSX: XLSXModule = await import("xlsx")
  const header = columns.map((c) => c.title)
  const rows = data.map((row) => columns.map((c) => row[c.key] ?? ""))
  const wsData = [header, ...rows]

  const ws = XLSX.utils.aoa_to_sheet(wsData)

  // Auto-fit column widths
  ws["!cols"] = columns.map((col) => {
    const maxLen = Math.max(
      col.title.length,
      ...data.map((row) => String(row[col.key] ?? "").length)
    )
    return { wch: Math.min(maxLen + 2, 40) }
  })

  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1")

  const name = filename.endsWith(".xlsx") ? filename : `${filename}.xlsx`
  XLSX.writeFile(wb, name)
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}
