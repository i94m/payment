"use client"

import * as React from "react"
import { notifyLogsApi } from "@/api"
import type { NotifyLogData } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  ListError,
  actionsColumn,
  secondaryCell,
  dateCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconEye } from "@tabler/icons-react"

export function NotifyLogsPage() {

  const [kindFilter, setKindFilter] = React.useState("")
  const [successFilter, setSuccessFilter] = React.useState("")
  const [detail, setDetail] = React.useState<NotifyLogData | null>(null)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (kindFilter !== "") params.kind = kindFilter
    if (successFilter !== "") params.success = successFilter
    return params
  }, [kindFilter, successFilter])

  const handleReset = () => {
    setKeyword("")
    setKindFilter("")
    setSuccessFilter("")
    applyFilters()
  }

  const kindLabel = (kind: string) =>
    kind === "merchant" ? "通知商户" : "上游回调"

  const {
    table, total, loading, error, keyword, setKeyword, applyFilters, refresh,
  } = useDataList<NotifyLogData>({
    // keyword 统一映射为后端认的 trade_no 过滤
    fetchFn: (params) => {
      const { keyword: kw, ...rest } = params as { keyword?: string }
      return notifyLogsApi.list({ ...rest, trade_no: kw || "" })
    },
    filters: filterParams,
    columns: [
      {
        accessorKey: "id",
        enableSorting: false,
        header: () => "ID",
        cell: ({ getValue }) => secondaryCell(getValue() as number),
        meta: { headClassName: "w-[70px]", sticky: "left" },
      },
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "创建时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]" },
      },
      {
        accessorKey: "kind",
        enableSorting: false,
        header: () => "类型",
        cell: ({ row }) => <span className="text-sm">{kindLabel(row.original.kind)}</span>,
        meta: { headClassName: "w-[100px]" },
      },
      {
        accessorKey: "trade_no",
        enableSorting: false,
        header: () => "订单号",
        cell: ({ row }) => truncatedCell(row.original.trade_no, 140),
        meta: { headClassName: "min-w-[150px]" },
      },
      {
        accessorKey: "url",
        enableSorting: false,
        header: () => "URL",
        cell: ({ row }) => truncatedCell(row.original.url, 200),
        meta: { headClassName: "min-w-[180px]" },
      },
      {
        accessorKey: "attempt",
        enableSorting: false,
        header: () => "尝试次数",
        cell: ({ getValue }) => secondaryCell(getValue() as number),
        meta: { headClassName: "w-[70px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "http_status",
        enableSorting: false,
        header: () => "HTTP 状态",
        cell: ({ row }) => secondaryCell(row.original.http_status ?? "—"),
        meta: { headClassName: "w-[90px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "success",
        enableSorting: false,
        header: () => "结果",
        cell: ({ row }) => (
          <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${row.original.success ? "bg-emerald-500/10 text-emerald-600" : "bg-destructive/10 text-destructive"}`}>
            {row.original.success ? "成功" : "失败"}
          </span>
        ),
        meta: { headClassName: "w-[80px] text-center", cellClassName: "text-center" },
      },
      actionsColumn([
        {
          label: "详情",
          icon: <IconEye className="size-4" />,
          onClick: (item) => setDetail(item),
        },
      ] as ActionItem<NotifyLogData>[], { width: 100 }),
    ],
    perPage: 20,
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 items-end">
            <div className="col-span-1">
              <Input
                placeholder={"搜索订单号..."}
                className="h-9"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
              />
            </div>
            <Select value={kindFilter || "_all"} onValueChange={(v) => setKindFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"类型"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部类型"}</SelectItem>
                <SelectItem value="merchant">{"通知商户"}</SelectItem>
                <SelectItem value="upstream">{"上游回调"}</SelectItem>
              </SelectContent>
            </Select>
            <Select value={successFilter || "_all"} onValueChange={(v) => setSuccessFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"结果"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部状态"}</SelectItem>
                <SelectItem value="1">{"成功"}</SelectItem>
                <SelectItem value="0">{"失败"}</SelectItem>
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
          {error ? (
            <ListError error={error} onRetry={refresh} />
          ) : (
            <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
              <DataTablePagination table={table} total={total} />
            </DataTable>
          )}
        </div>
      </div>

      <Dialog open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"详情"}</DialogTitle>
            <DialogDescription className="font-mono text-xs">
              {detail?.trade_no} · {detail && kindLabel(detail.kind)} · #{detail?.id}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-3 text-sm">
              <div>
                <p className="text-xs text-muted-foreground mb-1">URL</p>
                <pre className="whitespace-pre-wrap break-all rounded-lg bg-muted p-3 font-mono text-xs">{detail.url}</pre>
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-1">{"请求"}</p>
                <pre className="whitespace-pre-wrap break-all rounded-lg bg-muted p-3 font-mono text-xs max-h-48 overflow-y-auto">
                  {detail.request || "—"}
                </pre>
              </div>
              <div>
                <p className="text-xs text-muted-foreground mb-1">{"响应"}（HTTP {detail.http_status ?? "—"}）</p>
                <pre className="whitespace-pre-wrap break-all rounded-lg bg-muted p-3 font-mono text-xs max-h-48 overflow-y-auto">
                  {detail.response || "—"}
                </pre>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
