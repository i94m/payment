"use client"

import * as React from "react"
import { recordsApi } from "@/api"
import type { MerchantRecordData } from "@/api"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import { useDataList, ListError, dateCell, secondaryCell, truncatedCell } from "@/components/data-list/"
import { Button } from "@/components/ui/button"
import { IconRefresh } from "@tabler/icons-react"

const BIZ_TYPE_LABELS: Record<string, string> = {
  pay: "收款",
  settle: "结算",
  settle_reject: "结算驳回",
  refund: "退款",
  agent: "代理分润",
  adjust: "人工调账",
  recharge: "充值",
}

export function RecordsPage() {
  const { table, total, loading, error, refresh } = useDataList<MerchantRecordData>({
    fetchFn: (params) => recordsApi.list({ page: params.page, per_page: params.per_page }),
    columns: [
      { accessorKey: "created_at", enableSorting: false, header: () => "创建时间", cell: ({ row }) => dateCell(row.original.created_at), meta: { headClassName: "min-w-[150px]" } },
      {
        accessorKey: "biz_type",
        enableSorting: false,
        header: () => "业务类型",
        cell: ({ row }) => {
          const raw = String(row.original.biz_type ?? "")
          return secondaryCell(row.original.biz_type_name || BIZ_TYPE_LABELS[raw] || raw || "—")
        },
      },
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
        meta: { cellClassName: "text-right" },
      },
      { accessorKey: "balance_after", enableSorting: false, header: () => "变动后", cell: ({ row }) => secondaryCell(`¥${row.original.balance_after}`), meta: { cellClassName: "text-right" } },
      { accessorKey: "order_trade_no", enableSorting: false, header: () => "关联订单", cell: ({ row }) => truncatedCell(row.original.order_trade_no || "—", 120) },
      { accessorKey: "remark", enableSorting: false, header: () => "备注", cell: ({ row }) => truncatedCell(row.original.remark || "—", 140) },
    ],
    perPage: 15,
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard className="flex-row justify-end">
          <Button variant="outline" size="sm" disabled={loading} onClick={refresh}>
            <IconRefresh className="size-4" />
            {"刷新"}
          </Button>
        </FilterCard>
        <div className="px-4 lg:px-6">
          {error ? (
            <ListError error={error} onRetry={refresh} />
          ) : (
            <DataTable table={table} loading={loading} emptyMessage={"暂无资金明细"}>
              <DataTablePagination table={table} total={total} />
            </DataTable>
          )}
        </div>
      </div>
    </div>
  )
}
