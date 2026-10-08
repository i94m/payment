"use client"

import * as React from "react"
import { recordsApi } from "@/api"
import type { MerchantRecordData } from "@/api"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { useReactTable, getCoreRowModel, type ColumnDef } from "@tanstack/react-table"
import { dateCell, secondaryCell, truncatedCell } from "@/components/data-list/"
import { toast } from "sonner"

const PER_PAGE = 15

export function RecordsPage() {
  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <div className="px-4 lg:px-6">
          <RecordsTable />
        </div>
      </div>
    </div>
  )
}

function RecordsTable() {
  const [data, setData] = React.useState<MerchantRecordData[]>([])
  const [total, setTotal] = React.useState(0)
  const [page] = React.useState(1)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    setLoading(true)
    recordsApi.list({ page, per_page: PER_PAGE })
      .then((res) => { setData(res.items); setTotal(res.total) })
      .catch(() => toast.error("加载失败"))
      .finally(() => setLoading(false))
  }, [page])

  const columns = React.useMemo<ColumnDef<MerchantRecordData>[]>(() => [
    { accessorKey: "created_at", enableSorting: false, header: () => "创建时间", cell: ({ row }) => dateCell(row.original.created_at), meta: { headClassName: "min-w-[150px]" } },
    { accessorKey: "biz_type", enableSorting: false, header: () => "业务类型", cell: ({ getValue }) => secondaryCell(String(getValue())) },
    {
      accessorKey: "amount",
      enableSorting: false,
      header: () => "金额",
      cell: ({ row }) => {
        const v = Number(row.original.amount)
        return (
          <span className={`text-sm font-medium tabular-nums ${v >= 0 ? "text-emerald-600" : "text-rose-500"}`}>
            {v >= 0 ? "+" : ""}{v.toFixed(2)}
          </span>
        )
      },
    },
    { accessorKey: "balance_after", enableSorting: false, header: () => "变动后", cell: ({ row }) => secondaryCell(`¥${row.original.balance_after}`) },
    { accessorKey: "order_trade_no", enableSorting: false, header: () => "关联订单", cell: ({ row }) => truncatedCell(row.original.order_trade_no || "—", 120) },
    { accessorKey: "remark", enableSorting: false, header: () => "备注", cell: ({ row }) => truncatedCell(row.original.remark || "—", 140) },
  ], [])

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    pageCount: Math.ceil(total / PER_PAGE) || 1,
    state: { pagination: { pageIndex: page - 1, pageSize: PER_PAGE } },
  })

  return (
    <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
      <DataTablePagination table={table} />
    </DataTable>
  )
}
