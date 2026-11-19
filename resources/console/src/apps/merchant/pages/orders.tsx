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
import { PayIcon } from "@/components/pay-icon"
import {
  useDataList,
  ListError,
  actionsColumn,
  dateCell,
  copyableCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem, StatusBadgeOption } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconBell, IconEye, IconDownload, IconLoader2 } from "@tabler/icons-react"
import apiClient from "@/lib/api-client"
import { getErrorMessage } from "@/lib/error"
import { downloadBlob } from "@/lib/export"
import { EXPORT_ENABLED } from "@/lib/flags"
import { toIntParam } from "@/lib/form-utils"
import { toast } from "sonner"

// 状态口径与后端一致（0 待支付 / 1 已支付 / 2 已退款 / 3 已过期）；
// 扣量单由后端按 status=0 返回，不在此处额外推断。
const orderStatusOptions: StatusBadgeOption[] = [
  { label: "待支付", value: 0, variant: "secondary" },
  { label: "已支付", value: 1, variant: "default" },
  { label: "已退款", value: 2, variant: "outline" },
  { label: "已过期", value: 3, variant: "outline" },
]

/**
 * 详情接口（GET /order/{trade_no}）复用开放 API 查单字段：
 * 商品名/时间/退款信息分别是 name/addtime/endtime/refund_*，与列表字段名不同，
 * 这里做一次兜底映射，避免详情弹窗出现一列「—」。
 */
type OrderDetailData = OrderData & {
  name?: string
  addtime?: string
  endtime?: string
  refund_status?: "NONE" | "PARTIAL" | "FULL"
  refunded_amount?: string
}

const REFUND_LABELS: Record<string, string> = {
  NONE: "未退款",
  PARTIAL: "部分退款",
  FULL: "全额退款",
}

const STATUS_CLASS: Record<string, string> = {
  default: "bg-primary/10 text-primary",
  secondary: "bg-secondary text-secondary-foreground",
  destructive: "bg-destructive/10 text-destructive",
  outline: "border text-muted-foreground",
}

function StatusCell({ status }: { status: number }) {
  const opt = orderStatusOptions.find((o) => o.value === status)
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[opt?.variant ?? "outline"]}`}
    >
      {opt?.label ?? `未知(${status})`}
    </span>
  )
}

/** 通知状态：-1 失败 / 0 成功 / ≥1 重试中；未支付订单不展示 */
function NotifyStatusCell({ status, notifyStatus }: { status: number; notifyStatus?: number }) {
  if (status !== 1) return <span className="text-xs text-muted-foreground">—</span>
  const ns = notifyStatus ?? 0
  const map: Record<string, { label: string; cls: string }> = {
    failed: { label: "失败", cls: "bg-destructive/10 text-destructive" },
    ok: { label: "已通知", cls: "bg-primary/10 text-primary" },
    retry: { label: "重试中", cls: "bg-amber-500/10 text-amber-600" },
  }
  const item = ns < 0 ? map.failed : ns === 0 ? map.ok : map.retry
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${item.cls}`}>
      {item.label}
    </span>
  )
}

export function OrdersPage() {
  const [outTradeNo, setOutTradeNo] = React.useState("")
  const [statusFilter, setStatusFilter] = React.useState("")
  const [startDate, setStartDate] = React.useState<Date | undefined>(undefined)
  const [endDate, setEndDate] = React.useState<Date | undefined>(undefined)
  const [detailOrder, setDetailOrder] = React.useState<OrderDetailData | null>(null)
  const [detailOpen, setDetailOpen] = React.useState(false)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [notifyTarget, setNotifyTarget] = React.useState<string | null>(null)
  const [exporting, setExporting] = React.useState(false)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (outTradeNo.trim()) params.out_trade_no = outTradeNo.trim()
    const status = toIntParam(statusFilter)
    if (status !== undefined) params.status = status
    if (startDate && endDate) {
      params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
    }
    return params
  }, [outTradeNo, statusFilter, startDate, endDate])

  const handleReset = () => {
    setKeyword(""); setOutTradeNo(""); setStatusFilter(""); setStartDate(undefined); setEndDate(undefined)
    applyFilters()
  }

  /** 查询前拦截倒置的日期区间，避免把空结果误读成「没有订单」 */
  const handleSearch = () => {
    if (startDate && endDate && startDate > endDate) {
      toast.error("开始日期不能晚于结束日期")
      return
    }
    applyFilters()
  }

  const openDetail = async (tradeNo: string) => {
    setDetailOpen(true)
    setDetailOrder(null)
    setDetailLoading(true)
    try {
      setDetailOrder(await ordersApi.detail(tradeNo) as OrderDetailData)
    } catch (err) {
      setDetailOpen(false)
      toast.error(getErrorMessage(err, "订单详情加载失败"))
    } finally {
      setDetailLoading(false)
    }
  }

  const doNotify = async () => {
    if (!notifyTarget) return
    try {
      await ordersApi.resendNotify(notifyTarget)
      toast.success("通知已重新发送")
      setNotifyTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "通知发送失败"))
    }
  }

  const exportExcel = async () => {
    setExporting(true)
    try {
      // 导出走 Excel 接口（POST，带当前筛选；需 Authorization 头，故用 axios blob 下载）
      const blob = await apiClient.post("/order/export", filterParams, { responseType: "blob" })
      downloadBlob(blob as unknown as Blob, `orders-${Date.now()}.xlsx`)
    } catch (err) {
      toast.error(getErrorMessage(err, "导出失败，请稍后重试"))
    } finally {
      setExporting(false)
    }
  }

  const {
    table, total, loading, error, keyword, setKeyword, applyFilters, summary, refresh,
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
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5 text-sm">
            <PayIcon code={row.original.type} icon={row.original.type_icon} />
            {row.original.type_name || row.original.type}
          </span>
        ),
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
        cell: ({ row }) => <StatusCell status={row.original.status} />,
        meta: { headClassName: "w-[80px]" },
      },
      {
        accessorKey: "notify_status",
        enableSorting: false,
        header: () => "通知状态",
        cell: ({ row }) => (
          <NotifyStatusCell status={row.original.status} notifyStatus={row.original.notify_status} />
        ),
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
          onClick: (item) => { void openDetail(item.trade_no) },
        },
        {
          label: "重发通知",
          icon: <IconBell className="size-4" />,
          title: "仅已支付订单可重发通知",
          onClick: (item) => {
            // 未支付/过期订单没有可重发的通知：直接提示，避免无意义的失败请求
            if (item.status !== 1) {
              toast.info("仅已支付订单可重发通知")
              return
            }
            setNotifyTarget(item.trade_no)
          },
        },
      ] as ActionItem<OrderData>[], { width: 230 }),
    ],
    perPage: 20,
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
                onKeyDown={(e) => { if (e.key === "Enter") handleSearch() }}
              />
            </div>
            <Input
              placeholder={"商户订单号"}
              className="h-9"
              value={outTradeNo}
              onChange={(e) => setOutTradeNo(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleSearch() }}
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

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={handleSearch}>
              <IconSearch className="size-4" />
              {"查询"}
            </Button>
            <Button variant="outline" size="sm" onClick={handleReset}>
              <IconRefresh className="size-4" />
              {"重置"}
            </Button>
            <Button variant="ghost" size="sm" disabled={loading} onClick={refresh}>
              <IconRefresh className="size-4" />
              {"刷新"}
            </Button>
            {EXPORT_ENABLED && (
              <Button
                variant="outline"
                size="sm"
                className="ml-auto"
                disabled={exporting}
                onClick={exportExcel}
              >
                {exporting ? <IconLoader2 className="size-4 animate-spin" /> : <IconDownload className="size-4" />}
                {"导出Excel"}
              </Button>
            )}
          </div>

          {/* 当前筛选统计汇总 */}
          {summary && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-muted-foreground">
              <span>{"笔数"}: <b className="tabular-nums">{String(summary.count ?? 0)}</b></span>
              <span>{"金额"}: <b className="tabular-nums">¥{String(summary.money ?? "0.00")}</b></span>
              <span>{"商户到账"}: <b className="tabular-nums">¥{String(summary.income ?? "0.00")}</b></span>
              <span className="text-muted-foreground/70">
                {statusFilter === "" ? "（仅统计已支付订单）" : "（按所选状态统计）"}
              </span>
            </div>
          )}
        </FilterCard>

        <div className="px-4 lg:px-6">
          {error ? (
            <ListError error={error} onRetry={refresh} />
          ) : (
            <DataTable table={table} loading={loading} emptyMessage={"暂无订单记录"}>
              <DataTablePagination table={table} total={total} />
            </DataTable>
          )}
        </div>
      </div>

      {/* 订单详情 */}
      <Dialog
        open={detailOpen}
        onOpenChange={(open) => {
          if (!open) { setDetailOpen(false); setDetailOrder(null) }
        }}
      >
        <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"订单详情"}</DialogTitle>
            <DialogDescription className="font-mono text-xs">
              {detailOrder?.trade_no ?? "加载中…"}
            </DialogDescription>
          </DialogHeader>
          {detailLoading || !detailOrder ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <IconLoader2 className="size-4 animate-spin" />
              {"正在加载订单详情…"}
            </div>
          ) : (
            <div className="text-sm">
              <div className="flex items-center justify-between py-1.5">
                <span className="text-muted-foreground">{"状态"}</span>
                <StatusCell status={detailOrder.refund_status === "FULL" ? 2 : detailOrder.status} />
              </div>
              <Separator />
              <Row label={"商品名称"} value={detailOrder.subject || detailOrder.name} wrap />
              <Row label={"支付方式"} value={(
                <span className="inline-flex items-center gap-1.5">
                  <PayIcon code={detailOrder.type} icon={detailOrder.type_icon} />
                  {detailOrder.type_name || detailOrder.type}
                </span>
              )} />
              <Row label={"金额"} value={`¥${detailOrder.money}`} />
              <Row label={"实付金额"} value={detailOrder.realmoney ? `¥${detailOrder.realmoney}` : "—"} />
              <Row label={"商户到账"} value={detailOrder.merchant_income ? `¥${detailOrder.merchant_income}` : "—"} />
              <Row
                label={"退款信息"}
                value={
                  detailOrder.refund_status && detailOrder.refund_status !== "NONE"
                    ? `${REFUND_LABELS[detailOrder.refund_status] ?? detailOrder.refund_status} ¥${detailOrder.refunded_amount ?? "0.00"}`
                    : "未退款"
                }
              />
              <Row label={"商户订单号"} value={detailOrder.out_trade_no} mono />
              <Row label={"扩展参数"} value={detailOrder.param} mono wrap />
              <Row label={"创建时间"} value={detailOrder.created_at || detailOrder.addtime} />
              <Row label={"支付时间"} value={detailOrder.paid_at || detailOrder.endtime || "—"} />
              <Row label={"通知状态"} value={<NotifyStatusCell status={detailOrder.status} notifyStatus={detailOrder.notify_status} />} />
            </div>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!notifyTarget}
        onOpenChange={(open) => { if (!open) setNotifyTarget(null) }}
        title={"重发通知"}
        description={`确认向商户重新发送该订单的异步通知？${notifyTarget ? `（${notifyTarget}）` : ""}`}
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={doNotify}
      />
    </div>
  )
}

function Row({ label, value, mono, wrap }: { label: string; value?: React.ReactNode; mono?: boolean; wrap?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className={`text-right ${mono ? "font-mono text-xs" : ""} ${wrap ? "break-all" : "truncate"}`}>
        {value || "—"}
      </span>
    </div>
  )
}
