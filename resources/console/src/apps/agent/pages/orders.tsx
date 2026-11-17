"use client"

import * as React from "react"
import { agentPanelApi } from "@/api"
import type { AgentOrderData } from "@/api"
import { format } from "date-fns"
import { Button } from "@/components/ui/button"
import { DatePicker } from "@/components/ui/date-picker"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  copyableCell,
  dateCell,
  truncatedCell,
} from "@/components/data-list/"
import type { StatusBadgeOption } from "@/components/data-list/"
import { IconSearch, IconRefresh } from "@tabler/icons-react"

const orderStatusOptions: StatusBadgeOption[] = [
  { label: "待支付", value: 0, variant: "secondary" },
  { label: "已支付", value: 1, variant: "default" },
  { label: "已退款", value: 2, variant: "destructive" },
  { label: "已过期", value: 3, variant: "outline" },
]

export function OrdersPage() {
  const [startDate, setStartDate] = React.useState<Date | undefined>(undefined)
  const [endDate, setEndDate] = React.useState<Date | undefined>(undefined)
  const [statusFilter, setStatusFilter] = React.useState("")

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (startDate) params.starttime = format(startDate, "yyyy-MM-dd")
    if (endDate) params.endtime = format(endDate, "yyyy-MM-dd")
    if (statusFilter !== "") params.status = Number(statusFilter)
    return params
  }, [startDate, endDate, statusFilter])

  const handleReset = () => {
    setKeyword(""); setStartDate(undefined); setEndDate(undefined); setStatusFilter("")
    applyFilters()
  }

  const {
    table, loading, setKeyword, applyFilters,
  } = useDataList<AgentOrderData>({
    fetchFn: (params) => agentPanelApi.orders(params),
    filters: filterParams,
    columns: [
      {
        accessorKey: "trade_no",
        enableSorting: false,
        header: () => "订单号",
        cell: ({ row }) => copyableCell(row.original.trade_no),
        meta: { headClassName: "min-w-[170px]" },
      },
      {
        accessorKey: "money",
        enableSorting: false,
        header: () => "金额",
        cell: ({ row }) => <span className="text-sm font-medium tabular-nums">¥{row.original.money}</span>,
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "realmoney",
        enableSorting: false,
        header: () => "实收金额",
        cell: ({ row }) => (
          <span className="text-sm tabular-nums">
            {row.original.realmoney ? `¥${row.original.realmoney}` : "—"}
          </span>
        ),
        meta: { headClassName: "w-[100px]" },
      },
      {
        accessorKey: "subject",
        enableSorting: false,
        header: () => "商品名称",
        cell: ({ row }) => truncatedCell(row.original.subject, 120),
        meta: { headClassName: "min-w-[120px]" },
      },
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "创建时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]" },
      },
      {
        accessorKey: "paid_at",
        enableSorting: false,
        header: () => "支付时间",
        cell: ({ row }) => dateCell(row.original.paid_at, "—"),
        meta: { headClassName: "min-w-[150px]" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => {
          const opt = orderStatusOptions.find((o) => o.value === row.original.status)
          const variantCls: Record<string, string> = {
            default: "bg-primary/10 text-primary",
            secondary: "bg-secondary text-secondary-foreground",
            destructive: "bg-destructive/10 text-destructive",
            outline: "border text-muted-foreground",
          }
          return (
            <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${variantCls[opt?.variant ?? "default"]}`}>
              {opt?.label ?? row.original.status}
            </span>
          )
        },
        meta: { headClassName: "w-[80px]" },
      },
    ],
    perPage: 15,
    onError: () => {},
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 items-end">
            <DatePicker
              className="h-9"
              placeholder={"开始日期"}
              value={startDate}
              onChange={setStartDate}
            />
            <DatePicker
              className="h-9"
              placeholder={"结束日期"}
              value={endDate}
              onChange={setEndDate}
            />
            <Select value={statusFilter || "_all"} onValueChange={(v) => setStatusFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"状态"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部状态"}</SelectItem>
                {orderStatusOptions.map((o) => (
                  <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => applyFilters()}>
              <IconSearch className="size-4" />
              {"查询"}
            </Button>
            <Button variant="outline" size="sm" onClick={handleReset}>
              <IconRefresh className="size-4" />
              {"重置"}
            </Button>
          </div>
        </FilterCard>

        <div className="px-4 lg:px-6">
          <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
            <DataTablePagination table={table} />
          </DataTable>
        </div>
      </div>
    </div>
  )
}
