"use client"

import * as React from "react"
import { useSearchParams } from "react-router-dom"
import { ordersApi } from "@/api"
import type { OrderData } from "@/api"
import { format } from "date-fns"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { DatePicker } from "@/components/ui/date-picker"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { ConfirmDialog } from "@/components/data-list/confirm-dialog"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import { PayIcon } from "@/components/pay-icon"
import { DetailTable } from "@/components/detail-table"
import { OrderStatusBadge } from "@/components/order-status"
import { NOTIFY_STATUS_CLASS, ORDER_STATUS_OPTIONS, STATUS_BADGE_BASE } from "@/lib/order-status"
import {
  useDataList,
  ListError,
  actionsColumn,
  dateCell,
  copyableCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconBell, IconEye, IconDownload, IconLoader2 } from "@tabler/icons-react"
import apiClient from "@/lib/api-client"
import { getErrorMessage } from "@/lib/error"
import { downloadBlob } from "@/lib/export"
import { EXPORT_ENABLED } from "@/lib/flags"
import { toIntParam } from "@/lib/form-utils"
import { toast } from "sonner"

// 扣量单由后端按 status=0 返回，不在此处额外推断；状态配色统一走 ORDER_STATUS_OPTIONS。

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

/** 通知状态：-1 失败 / 0 成功 / ≥1 重试中；未支付订单留空（空值不画横杠） */
function NotifyStatusCell({ status, notifyStatus }: { status: number; notifyStatus?: number }) {
  if (status !== 1) return null
  const ns = notifyStatus ?? 0
  const map = {
    failed: { label: "失败", cls: NOTIFY_STATUS_CLASS.failed },
    ok: { label: "已通知", cls: NOTIFY_STATUS_CLASS.ok },
    retry: { label: "重试中", cls: NOTIFY_STATUS_CLASS.retry },
  }
  const item = ns < 0 ? map.failed : ns === 0 ? map.ok : map.retry
  return (
    <span className={`${STATUS_BADGE_BASE} ${item.cls}`}>
      {item.label}
    </span>
  )
}

export function OrdersPage() {
  // 测试支付页跳转过来时带 ?order_type=2，直接落到测试单列表；默认全部类型（含测试单）
  const [searchParams] = useSearchParams()
  const [outTradeNo, setOutTradeNo] = React.useState("")
  const [orderType, setOrderType] = React.useState(searchParams.get("order_type") === "2" ? "2" : "")
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
    if (orderType) params.order_type = orderType
    const status = toIntParam(statusFilter)
    if (status !== undefined) params.status = status
    if (startDate && endDate) {
      params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
    }
    return params
  }, [outTradeNo, orderType, statusFilter, startDate, endDate])

  const handleReset = () => {
    setKeyword(""); setOutTradeNo(""); setOrderType(""); setStatusFilter("")
    setStartDate(undefined); setEndDate(undefined)
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
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5">
            {copyableCell(row.original.trade_no, 220)}
            {row.original.order_type === 2 && <Badge variant="secondary">{"测试"}</Badge>}
          </span>
        ),
        meta: { headClassName: "min-w-[240px]", sticky: "left" },
      },
      {
        accessorKey: "out_trade_no",
        enableSorting: false,
        header: () => "商户订单号",
        cell: ({ row }) => truncatedCell(row.original.out_trade_no, 200),
        meta: { headClassName: "min-w-[220px]" },
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
        meta: { headClassName: "w-[90px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "merchant_income",
        enableSorting: false,
        header: () => "商户到账",
        cell: ({ row }) => (
          <span className="text-sm tabular-nums">
            {row.original.merchant_income ? `¥${row.original.merchant_income}` : ""}
          </span>
        ),
        meta: { headClassName: "w-[100px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => <OrderStatusBadge status={row.original.status} />,
        meta: { headClassName: "w-[80px] text-center", cellClassName: "text-center" },
      },
      {
        accessorKey: "notify_status",
        enableSorting: false,
        header: () => "通知状态",
        cell: ({ row }) => (
          <NotifyStatusCell status={row.original.status} notifyStatus={row.original.notify_status} />
        ),
        meta: { headClassName: "w-[90px] text-center", cellClassName: "text-center" },
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
            // 测试单没有商户通知，直接提示，不发无意义请求
            if (item.order_type === 2) {
              toast.info("测试单不发送商户通知")
              return
            }
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
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3 items-end">
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
            <Select
              value={orderType || "_all"}
              onValueChange={(v) => setOrderType(v === "_all" ? "" : v)}
              items={[
                { value: "_all", label: "全部类型" },
                { value: "0", label: "正式收款" },
                { value: "2", label: "测试单" },
              ]}
            >
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"订单类型"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部类型"}</SelectItem>
                <SelectItem value="0">{"正式收款"}</SelectItem>
                <SelectItem value="2">{"测试单"}</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={statusFilter || "_all"}
              onValueChange={(v) => setStatusFilter(v === "_all" ? "" : v)}
              items={[
                { value: "_all", label: "全部状态" },
                ...ORDER_STATUS_OPTIONS.map((o) => ({ value: String(o.value), label: o.label })),
              ]}
            >
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"状态"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部状态"}</SelectItem>
                {ORDER_STATUS_OPTIONS.map((o) => (
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
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
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
            <MerchantOrderDetailTable order={detailOrder} />
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

/** 订单详情主体：与后台订单详情同一套「一行两组」表格，窄屏自动退化成一行一组 */
function MerchantOrderDetailTable({ order }: { order: OrderDetailData }) {
  // 全额退款时对外展示为「已退款」，与列表口径一致
  const status = order.refund_status === "FULL" ? 2 : order.status
  const refundText = order.refund_status && order.refund_status !== "NONE"
    ? `${REFUND_LABELS[order.refund_status] ?? order.refund_status} ¥${order.refunded_amount ?? "0.00"}`
    : "未退款"

  return (
    <DetailTable blankEmpty rows={[
      [
        { label: "状态", value: (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <OrderStatusBadge status={status} />
            {order.order_type === 2 && <Badge variant="secondary">{"测试单"}</Badge>}
          </span>
        ) },
        { label: "商品名称", value: order.subject || order.name, wrap: true },
      ],
      [
        { label: "支付方式", value: order.type_name || order.type,
          icon: <PayIcon code={order.type} icon={order.type_icon} /> },
        { label: "金额", value: `¥${order.money}` },
      ],
      [
        { label: "实付金额", value: order.realmoney ? `¥${order.realmoney}` : "" },
        { label: "商户到账", value: order.merchant_income ? `¥${order.merchant_income}` : "" },
      ],
      [
        { label: "退款信息", value: refundText },
        // 未支付订单没有通知可看，留空走统一占位符
        { label: "通知状态", value: order.status === 1
          ? <NotifyStatusCell status={order.status} notifyStatus={order.notify_status} />
          : "" },
      ],
      [
        { label: "商户订单号", value: order.out_trade_no, mono: true, wrap: true },
        { label: "创建时间", value: order.created_at || order.addtime },
      ],
      [
        { label: "支付时间", value: order.paid_at || order.endtime },
        { label: "扩展参数", value: order.param, mono: true, wrap: true },
      ],
    ]} />
  )
}
