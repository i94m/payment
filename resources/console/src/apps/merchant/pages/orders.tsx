"use client"

import * as React from "react"
import { ordersApi } from "@/api"
import type { OrderData } from "@/api"
import { format } from "date-fns"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DatePicker } from "@/components/ui/date-picker"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Separator } from "@/components/ui/separator"
import { ConfirmDialog } from "@/components/data-list/confirm-dialog"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  actionsColumn,
  dateCell,
  copyableCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem, StatusBadgeOption } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconBell, IconEye, IconDownload } from "@tabler/icons-react"
import apiClient from "@/lib/api-client"
import { toast } from "sonner"

const orderStatusOptions: StatusBadgeOption[] = [
  { label: "待支付", value: 0, variant: "secondary" },
  { label: "已支付", value: 1, variant: "default" },
  { label: "已过期", value: 3, variant: "outline" },
]

export function OrdersPage() {

  const [outTradeNo, setOutTradeNo] = React.useState("")
  const [statusFilter, setStatusFilter] = React.useState("")
  const [startDate, setStartDate] = React.useState<Date | undefined>(undefined)
  const [endDate, setEndDate] = React.useState<Date | undefined>(undefined)
  const [detailOrder, setDetailOrder] = React.useState<OrderData | null>(null)
  const [notifyTarget, setNotifyTarget] = React.useState<string | null>(null)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (outTradeNo.trim()) params.out_trade_no = outTradeNo.trim()
    if (statusFilter !== "") params.status = Number(statusFilter)
    if (startDate && endDate) {
      params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
    }
    return params
  }, [outTradeNo, statusFilter, startDate, endDate])

  const handleReset = () => {
    setKeyword(""); setOutTradeNo(""); setStatusFilter(""); setStartDate(undefined); setEndDate(undefined)
    applyFilters()
  }

  const doNotify = async () => {
    if (!notifyTarget) return
    try {
      await ordersApi.resendNotify(notifyTarget)
      toast.success("通知发送成功")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setNotifyTarget(null)
    }
  }

  const {
    table, total, loading, keyword, setKeyword, applyFilters, summary,
  } = useDataList<OrderData>({
    // keyword（平台订单号搜索框）映射为后端认的 trade_no
    fetchFn: (params) => {
      const { keyword: kw, ...rest } = params as { keyword?: string }
      return ordersApi.list({ ...rest, trade_no: kw || "" } as never)
    },
    filters: filterParams,
    columns: [
      {
        accessorKey: "trade_no",
        enableSorting: false,
        header: () => "平台订单号",
        cell: ({ row }) => copyableCell(row.original.trade_no),
        meta: { headClassName: "min-w-[170px]", sticky: "left" },
      },
      {
        accessorKey: "out_trade_no",
        enableSorting: false,
        header: () => "商户订单号",
        cell: ({ row }) => truncatedCell(row.original.out_trade_no, 120),
        meta: { headClassName: "min-w-[130px]" },
      },
      {
        accessorKey: "type",
        enableSorting: false,
        header: () => "支付方式",
        cell: ({ getValue }) => <span className="text-sm">{getValue() as string}</span>,
        meta: { headClassName: "w-[80px]" },
      },
      {
        accessorKey: "subject",
        enableSorting: false,
        header: () => "商品名称",
        cell: ({ row }) => truncatedCell(row.original.subject, 120),
        meta: { headClassName: "min-w-[120px]" },
      },
      {
        accessorKey: "money",
        enableSorting: false,
        header: () => "金额",
        cell: ({ row }) => <span className="text-sm font-medium tabular-nums">¥{row.original.money}</span>,
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "merchant_income",
        enableSorting: false,
        header: () => "商户到账",
        cell: ({ row }) => (
          <span className="text-sm tabular-nums">
            {row.original.merchant_income ? `¥${row.original.merchant_income}` : "—"}
          </span>
        ),
        meta: { headClassName: "w-[100px]" },
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
      {
        accessorKey: "notify_status",
        enableSorting: false,
        header: () => "通知状态",
        cell: ({ row }) => {
          const ns = row.original.notify_status ?? 0
          const map: Record<number, { label: string; cls: string }> = {
            [-1]: { label: "失败", cls: "bg-destructive/10 text-destructive" },
            0: { label: ns === 0 && row.original.status !== 1 ? "—" : "已通知", cls: "bg-primary/10 text-primary" },
          }
          const item = map[ns] ?? { label: "重试中", cls: "bg-amber-500/10 text-amber-600" }
          if (row.original.status !== 1) return <span className="text-xs text-muted-foreground">—</span>
          return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${item.cls}`}>{item.label}</span>
        },
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "创建时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]" },
      },
      actionsColumn([
        {
          label: "详情",
          icon: <IconEye className="size-4" />,
          onClick: async (item) => {
            try {
              setDetailOrder(await ordersApi.detail(item.trade_no))
            } catch {
              toast.error("加载失败")
            }
          },
        },
        {
          label: "重发通知",
          icon: <IconBell className="size-4" />,
          separatorBefore: true,
          onClick: (item) => setNotifyTarget(item.trade_no),
        },
      ] as ActionItem<OrderData>[]),
    ],
    perPage: 20,
    onError: () => toast.error("操作失败"),
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
            <div className="col-span-1 lg:col-span-2">
              <Input
                placeholder={"平台订单号"}
                className="h-9"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
              />
            </div>
            <Input
              placeholder={"商户订单号"}
              className="h-9"
              value={outTradeNo}
              onChange={(e) => setOutTradeNo(e.target.value)}
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
            <Button
              variant="outline"
              size="sm"
              className="ml-auto"
              onClick={async () => {
                // 导出走 CSV 接口（POST，带当前筛选；需 Authorization 头，故用 axios blob 下载）
                try {
                  const blob = await apiClient.post("/order/export", filterParams, { responseType: "blob" })
                  const url = URL.createObjectURL(blob as unknown as Blob)
                  const a = document.createElement("a")
                  a.href = url
                  a.download = `orders-${Date.now()}.csv`
                  a.click()
                  URL.revokeObjectURL(url)
                } catch {
                  toast.error("操作失败")
                }
              }}
            >
              <IconDownload className="size-4" />
              {"导出CSV"}
            </Button>
          </div>

          {/* 当前筛选统计汇总 */}
          {summary && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-muted-foreground">
              <span>{"笔数"}: <b className="tabular-nums">{String(summary.count ?? 0)}</b></span>
              <span>{"金额"}: <b className="tabular-nums">¥{String(summary.money ?? "0.00")}</b></span>
              <span>{"商户到账"}: <b className="tabular-nums">¥{String(summary.income ?? "0.00")}</b></span>
            </div>
          )}
        </FilterCard>

        <div className="px-4 lg:px-6">
          <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
            <DataTablePagination table={table} total={total} />
          </DataTable>
        </div>
      </div>

      {/* 订单详情 */}
      <Dialog open={!!detailOrder} onOpenChange={(open) => !open && setDetailOrder(null)}>
        <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"详情"}</DialogTitle>
            <DialogDescription className="font-mono text-xs">{detailOrder?.trade_no}</DialogDescription>
          </DialogHeader>
          {detailOrder && (
            <div className="text-sm">
              <div className="flex items-center justify-between py-1.5">
                <span className="text-muted-foreground">{"状态"}</span>
                <span className="font-medium">{orderStatusOptions.find((o) => o.value === detailOrder.status)?.label}</span>
              </div>
              <Separator />
              <Row label={"商品名称"} value={detailOrder.subject} />
              <Row label={"支付方式"} value={detailOrder.type} />
              <Row label={"金额"} value={`¥${detailOrder.money}`} />
              <Row label={"实付金额"} value={detailOrder.realmoney ? `¥${detailOrder.realmoney}` : "—"} />
              <Row label={"商户到账"} value={detailOrder.merchant_income ? `¥${detailOrder.merchant_income}` : "—"} />
              <Row label={"商户订单号"} value={detailOrder.out_trade_no} mono />
              <Row label={"扩展参数"} value={detailOrder.param} mono wrap />
              <Row label={"创建时间"} value={detailOrder.created_at} />
              <Row label={"支付时间"} value={detailOrder.paid_at || "—"} />
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!notifyTarget}
        onOpenChange={(open) => { if (!open) setNotifyTarget(null) }}
        title={"重发通知"}
        description={"确认重发商户异步通知？"}
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={doNotify}
      />
    </div>
  )
}

function Row({ label, value, mono, wrap }: { label: string; value?: string | null; mono?: boolean; wrap?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className={`text-right ${mono ? "font-mono text-xs" : ""} ${wrap ? "break-all" : "truncate"}`} title={value ?? ""}>
        {value || "—"}
      </span>
    </div>
  )
}
