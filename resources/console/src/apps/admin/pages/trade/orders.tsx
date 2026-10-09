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
  secondaryCell,
  dateCell,
  copyableCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem, StatusBadgeOption } from "@/components/data-list/"
import {
  IconSearch, IconRefresh, IconBell, IconRefreshDot, IconDownload, IconEye,
} from "@tabler/icons-react"
import { toast } from "sonner"
import apiClient from "@/lib/api-client"
import { useSearchParams } from "react-router-dom"

const orderStatusOptions: StatusBadgeOption[] = [
  { label: "待支付", value: 0, variant: "secondary" },
  { label: "已支付", value: 1, variant: "default" },
  { label: "已过期", value: 3, variant: "outline" },
]

function statusBadge(status: number) {
  const opt = orderStatusOptions.find((o) => o.value === status)
  const variantCls: Record<string, string> = {
    default: "bg-primary/10 text-primary",
    secondary: "bg-secondary text-secondary-foreground",
    destructive: "bg-destructive/10 text-destructive",
    outline: "border text-muted-foreground",
  }
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${variantCls[opt?.variant ?? "default"]}`}>
      {opt?.label ?? status}
    </span>
  )
}

function moneyCell(v: string | null | undefined) {
  if (!v) return <span className="text-sm text-muted-foreground">—</span>
  return <span className="text-sm tabular-nums">¥{v}</span>
}

export function OrdersPage() {
  // 支持从商户列表快捷跳转：/trade/orders?merchant_id=xx；通道列表：?channel_id=xx；支付方式列表：?type=xx；
  // 资金明细关联订单号：?trade_no=xx
  const [searchParams] = useSearchParams()

  const [tradeNo, setTradeNo] = React.useState(searchParams.get("trade_no") || "")
  const [outTradeNo, setOutTradeNo] = React.useState("")
  const [merchantId, setMerchantId] = React.useState(searchParams.get("merchant_id") || "")
  const [channelId] = React.useState(searchParams.get("channel_id") || "")
  const [typeCode] = React.useState(searchParams.get("type") || "")
  const [statusFilter, setStatusFilter] = React.useState("")
  const [notifyFilter, setNotifyFilter] = React.useState("")
  const [startDate, setStartDate] = React.useState<Date | undefined>(undefined)
  const [endDate, setEndDate] = React.useState<Date | undefined>(undefined)

  const [detailOrder, setDetailOrder] = React.useState<OrderData | null>(null)
  const [actionTradeNo, setActionTradeNo] = React.useState<string | null>(null)
  const [actionType, setActionType] = React.useState<"refill" | "notify" | null>(null)
  const [summaryMoney, setSummaryMoney] = React.useState("")

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (tradeNo.trim()) params.trade_no = tradeNo.trim()
    if (outTradeNo.trim()) params.out_trade_no = outTradeNo.trim()
    if (merchantId.trim()) params.merchant_id = merchantId.trim()
    if (channelId.trim()) params.channel_id = Number(channelId)
    if (typeCode.trim()) params.type = typeCode.trim()
    if (statusFilter !== "") params.status = Number(statusFilter)
    if (notifyFilter !== "") params.notify_status = Number(notifyFilter)
    if (startDate && endDate) {
      params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
    }
    return params
  }, [tradeNo, outTradeNo, merchantId, typeCode, statusFilter, notifyFilter, startDate, endDate])

  const handleReset = () => {
    setTradeNo(""); setOutTradeNo(""); setMerchantId("")
    setStatusFilter(""); setNotifyFilter(""); setStartDate(undefined); setEndDate(undefined)
    applyFilters()
  }

  const {
    table, loading, keyword, setKeyword, applyFilters, refresh,
  } = useDataList<OrderData>({
    // useServerPagination 统一注入 keyword；后端 /order/list 只认 trade_no，此处做映射
    fetchFn: async (params) => {
      const { keyword: kw, ...rest } = params as { keyword?: string }
      const res = await ordersApi.list({ ...rest, trade_no: kw || "" } as never)
      if (res?.summary?.money !== undefined) {
        setSummaryMoney(String(res.summary.money))
      }
      return res
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
        accessorKey: "merchant_username",
        enableSorting: false,
        header: () => "商户",
        cell: ({ row }) => secondaryCell(row.original.merchant_username || row.original.merchant_id || "—"),
        meta: { headClassName: "min-w-[90px]" },
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
        accessorKey: "realmoney",
        enableSorting: false,
        header: () => "实付金额",
        cell: ({ row }) => moneyCell(row.original.realmoney),
        meta: { headClassName: "w-[100px]" },
      },
      {
        accessorKey: "profit",
        enableSorting: false,
        header: () => "平台利润",
        cell: ({ row }) => moneyCell(row.original.profit),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => statusBadge(row.original.status),
        meta: { headClassName: "w-[80px]" },
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
          label: "补单",
          icon: <IconRefreshDot className="size-4" />,
          onClick: (item) => { setActionType("refill"); setActionTradeNo(item.trade_no) },
        },
        {
          label: "重发通知",
          icon: <IconBell className="size-4" />,
          onClick: (item) => { setActionType("notify"); setActionTradeNo(item.trade_no) },
        },
      ] as ActionItem<OrderData>[], { width: 320 }),
    ],
    perPage: 20,
    onError: () => toast.error("操作失败"),
  })

  const handleExport = async () => {
    try {
      const params: Record<string, string> = {}
      if (tradeNo.trim()) params.trade_no = tradeNo.trim()
      if (outTradeNo.trim()) params.out_trade_no = outTradeNo.trim()
      if (merchantId.trim()) params.merchant_id = merchantId.trim()
      if (statusFilter !== "") params.status = statusFilter
      if (notifyFilter !== "") params.notify_status = notifyFilter
      if (startDate && endDate) {
        params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
      }
      const res = await apiClient.get("/order/export", { params, responseType: "blob" })
      const blob = res as unknown as Blob
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `orders_${Date.now()}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      toast.error("操作失败")
    }
  }

  const actionTitle = actionType === "refill" ? "补单" : "重发通知"
  const actionMessage = actionType === "refill" ? "确认手动补单？将跳过上游验证直接记账。"
    : "确认重发商户异步通知？"

  const runAction = async () => {
    if (!actionTradeNo || !actionType) return
    try {
      if (actionType === "refill") await ordersApi.refill(actionTradeNo)
      if (actionType === "notify") await ordersApi.resendNotify(actionTradeNo)
      toast.success(actionType === "refill" ? "补单成功" : "通知发送成功")
      refresh()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setActionTradeNo(null)
      setActionType(null)
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3 items-end">
            <div className="col-span-1">
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
            <Input
              placeholder={"商户 ID"}
              className="h-9"
              value={merchantId}
              onChange={(e) => setMerchantId(e.target.value)}
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
            <Select value={notifyFilter || "_all"} onValueChange={(v) => setNotifyFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"通知状态"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部通知状态"}</SelectItem>
                <SelectItem value="0">{"未通知"}</SelectItem>
                <SelectItem value="1">{"已通知"}</SelectItem>
                <SelectItem value="2">{"通知失败"}</SelectItem>
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

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => applyFilters()}>
                <IconSearch className="size-4" />
                {"查询"}
              </Button>
              <Button variant="outline" size="sm" onClick={handleReset}>
                <IconRefresh className="size-4" />
                {"重置"}
              </Button>
              <Button variant="outline" size="sm" onClick={handleExport}>
                <IconDownload className="size-4" />
                {"导出 CSV"}
              </Button>
            </div>
            {summaryMoney !== "" && (
              <div className="text-sm text-muted-foreground">
                {"筛选结果总金额"}：<span className="font-medium tabular-nums text-foreground">¥{summaryMoney}</span>
              </div>
            )}
          </div>
        </FilterCard>

        <div className="px-4 lg:px-6">
          <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
            <DataTablePagination table={table} />
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
                {statusBadge(detailOrder.status)}
              </div>
              <Separator />
              <DetailRow label={"商品名称"} value={detailOrder.subject} />
              <DetailRow label={"商户"} value={detailOrder.merchant_username || String(detailOrder.merchant_id ?? "")} />
              <DetailRow label={"金额"} value={`¥${detailOrder.money}`} />
              <DetailRow label={"实付金额"} value={detailOrder.realmoney ? `¥${detailOrder.realmoney}` : "—"} />
              <DetailRow label={"商户到账"} value={detailOrder.merchant_income ? `¥${detailOrder.merchant_income}` : "—"} />
              <DetailRow label={"平台利润"} value={detailOrder.profit ? `¥${detailOrder.profit}` : "—"} />
              <DetailRow label={"支付方式"} value={detailOrder.type} />
              <DetailRow label={"通道插件"} value={detailOrder.plugin_name} />
              <DetailRow label={"上游订单号"} value={detailOrder.api_trade_no} mono />
              <DetailRow label={"商户订单号"} value={detailOrder.out_trade_no} mono />
              <DetailRow label={"买家账号"} value={detailOrder.buyer_id} />
              <DetailRow label={"客户端 IP"} value={detailOrder.client_ip} mono />
              <DetailRow label={"扩展参数"} value={detailOrder.param} mono wrap />
              <DetailRow label={"通知地址"} value={detailOrder.notify_url} mono wrap />
              <DetailRow label={"回跳地址"} value={detailOrder.return_url} mono wrap />
              <DetailRow label={"通知次数"} value={String(detailOrder.notify_count ?? 0)} />
              <DetailRow label={"创建时间"} value={detailOrder.created_at} />
              <DetailRow label={"支付时间"} value={detailOrder.paid_at || "—"} />
              <DetailRow label={"过期时间"} value={detailOrder.expire_at || "—"} />
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* 补单/通知确认 */}
      <ConfirmDialog
        open={!!actionTradeNo}
        onOpenChange={(open) => { if (!open) { setActionTradeNo(null); setActionType(null) } }}
        title={actionTitle}
        description={actionMessage}
        variant="warning"
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={runAction}
      />
    </div>
  )
}

function DetailRow({ label, value, mono, wrap }: { label: string; value?: string | null; mono?: boolean; wrap?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-4 py-1.5">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className={`text-right ${mono ? "font-mono text-xs" : ""} ${wrap ? "break-all" : "truncate"}`} title={value ?? ""}>
        {value || "—"}
      </span>
    </div>
  )
}
