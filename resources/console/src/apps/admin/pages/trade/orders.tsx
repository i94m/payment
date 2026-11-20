"use client"

import * as React from "react"
import { ordersApi, payApi } from "@/api"
import type { ChannelCodeData, OrderData } from "@/api"
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
import { ORDER_STATUS_OPTIONS } from "@/lib/order-status"
import {
  useDataList,
  ListError,
  actionsColumn,
  secondaryCell,
  dateCell,
  copyableCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import {
  IconSearch, IconRefresh, IconBell, IconRefreshDot, IconDownload, IconEye,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { useAuthStore } from "@/stores/auth-store"
import { downloadBlob } from "@/lib/export"
import { EXPORT_ENABLED } from "@/lib/flags"
import { digitsOnly, toIntParam } from "@/lib/form-utils"
import apiClient from "@/lib/api-client"
import { usePermission } from "@/hooks/use-permission"
import { useSearchParams } from "react-router-dom"

function moneyCell(v: string | null | undefined) {
  if (!v) return <span className="text-sm text-muted-foreground">—</span>
  return <span className="text-sm tabular-nums">¥{v}</span>
}

export function OrdersPage() {
  // 支持从商户列表快捷跳转：/trade/orders?merchant_id=xx；通道列表：?channel_id=xx；支付方式列表：?type=xx；
  // 资金明细关联订单号：?trade_no=xx
  const [searchParams] = useSearchParams()
  // 动作按钮按细粒度权限显隐（接口侧 PermissionChecker 仍会兜底 403）
  const hasPermission = usePermission()
  const canExport = hasPermission("order.export")
  const canRefill = hasPermission("order.refill")
  const canNotify = hasPermission("order.notify")

  // 平台订单号统一走搜索框（keyword）：从别处跳转带 ?trade_no=xx 时作为首屏搜索词，
  // 输入框可见、可编辑，避免 URL 参数与实际筛选条件各存一份（重置后还残留）。
  const initialTradeNo = React.useMemo(
    () => searchParams.get("trade_no") || "", [searchParams],
  )
  const [outTradeNo, setOutTradeNo] = React.useState("")
  const [merchantId, setMerchantId] = React.useState(searchParams.get("merchant_id") || "")
  const [channelId] = React.useState(searchParams.get("channel_id") || "")
  // 下游通道编码筛选：编码列表「订单」快捷跳转带 ?channel_code_id=xx
  const [channelCodeId, setChannelCodeId] = React.useState(searchParams.get("channel_code_id") || "")
  const [channelCodes, setChannelCodes] = React.useState<ChannelCodeData[]>([])
  const [typeCode] = React.useState(searchParams.get("type") || "")
  // 订单类型：""=全部类型（默认）/ "0" 正式收款 / "1" 余额充值 / "2" 测试单
  const [orderType, setOrderType] = React.useState("")
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
    if (outTradeNo.trim()) params.out_trade_no = outTradeNo.trim()
    // 数字型过滤统一走 toIntParam：非法值（如 URL 里带 channel_id=abc）不再序列化成 NaN 打到后端
    const mid = toIntParam(merchantId)
    if (mid !== undefined) params.merchant_id = mid
    const cid = toIntParam(channelId)
    if (cid !== undefined) params.channel_id = cid
    const codeId = toIntParam(channelCodeId)
    if (codeId !== undefined) params.channel_code_id = codeId
    if (typeCode.trim()) params.type = typeCode.trim()
    if (orderType) params.order_type = orderType
    if (statusFilter !== "") params.status = Number(statusFilter)
    if (notifyFilter !== "") params.notify_status = Number(notifyFilter)
    if (startDate && endDate) {
      params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
    }
    return params
  }, [outTradeNo, merchantId, channelId, channelCodeId, typeCode, orderType,
      statusFilter, notifyFilter, startDate, endDate])

  // 日期区间是成对参数：只选一端会被后端静默忽略，这里显式提示而不是「查了没反应」
  const applyFilterParams = () => {
    if (!!startDate !== !!endDate) {
      toast.warning("请同时选择开始日期与结束日期")
      return
    }
    applyFilters()
  }

  // 编码下拉只是筛选辅助：没有「通道编码-查看」权限就别请求 —— 403 会被全局拦截器
  // 直接跳到错误页，订单列表反而打不开（编码列本身仍显示订单留痕的字符串）。
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const canViewCodes = !!user?.is_super_admin || permissions.includes("*")
    || permissions.includes("channel_code.view")
  React.useEffect(() => {
    if (!canViewCodes) return
    payApi.channelCodes({ page: 1, per_page: 100 })
      .then((res) => setChannelCodes(res.items))
      .catch(() => {})
  }, [canViewCodes])

  const handleReset = () => {
    setKeyword(""); setOutTradeNo(""); setMerchantId(""); setOrderType(""); setChannelCodeId("")
    setStatusFilter(""); setNotifyFilter(""); setStartDate(undefined); setEndDate(undefined)
    applyFilters()
  }

  const {
    table, total, loading, error, keyword, appliedKeyword, setKeyword, applyFilters, refresh,
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
    initialKeyword: initialTradeNo,
    columns: [
      {
        accessorKey: "trade_no",
        enableSorting: false,
        header: () => "平台订单号",
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5">
            {copyableCell(row.original.trade_no, 220)}
            {row.original.order_type === 2 && <Badge variant="secondary">{"测试"}</Badge>}
            {row.original.order_type === 1 && <Badge variant="outline">{"充值"}</Badge>}
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
        // 后端同时返回 type_name/type_icon（支付方式显示名与图标），缺失时退回调用值 + 内置图标
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5 text-sm">
            <PayIcon code={row.original.type} icon={row.original.type_icon} />
            {row.original.type_name || row.original.type}
          </span>
        ),
        meta: { headClassName: "w-[80px]" },
      },
      {
        accessorKey: "channel_code",
        enableSorting: false,
        header: () => "通道编码",
        cell: ({ row }) => (
          row.original.channel_code
            ? <span className="font-mono text-xs">{row.original.channel_code}</span>
            : <span className="text-sm text-muted-foreground">—</span>
        ),
        meta: { headClassName: "w-[90px]" },
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
        accessorKey: "realmoney",
        enableSorting: false,
        header: () => "实付金额",
        cell: ({ row }) => moneyCell(row.original.realmoney),
        meta: { headClassName: "w-[100px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "profit",
        enableSorting: false,
        header: () => "平台利润",
        cell: ({ row }) => moneyCell(row.original.profit),
        meta: { headClassName: "w-[90px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => <OrderStatusBadge status={row.original.status} />,
        meta: { headClassName: "w-[80px] text-center", cellClassName: "text-center" },
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
            } catch (err) {
              toast.error(getErrorMessage(err, "订单详情加载失败"))
            }
          },
        },
        ...(canRefill ? [{
          label: "补单",
          icon: <IconRefreshDot className="size-4" />,
          onClick: (item: OrderData) => { setActionType("refill"); setActionTradeNo(item.trade_no) },
        }] : []),
        ...(canNotify ? [{
          label: "重发通知",
          icon: <IconBell className="size-4" />,
          onClick: (item: OrderData) => { setActionType("notify"); setActionTradeNo(item.trade_no) },
        }] : []),
      ] as ActionItem<OrderData>[], { width: 264 }),
    ],
    perPage: 20,
  })

  const handleExport = async () => {
    try {
      const params: Record<string, string> = {}
      if (appliedKeyword) params.trade_no = appliedKeyword
      if (outTradeNo.trim()) params.out_trade_no = outTradeNo.trim()
      const mid = toIntParam(merchantId)
      if (mid !== undefined) params.merchant_id = String(mid)
      if (orderType) params.order_type = orderType
      if (statusFilter !== "") params.status = statusFilter
      if (notifyFilter !== "") params.notify_status = notifyFilter
      if (startDate && endDate) {
        params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
      }
      const res = await apiClient.get("/order/export", { params, responseType: "blob" })
      downloadBlob(res as unknown as Blob, `orders_${Date.now()}.xlsx`)
    } catch (err) {
      toast.error(getErrorMessage(err, "导出失败"))
    }
  }

  const actionTitle = actionType === "refill" ? "补单" : "重发通知"
  const actionMessage = actionType === "refill" ? "确认手动补单？将跳过上游验证直接记账。"
    : "确认重发商户异步通知？"

  const runAction = async () => {
    if (!actionTradeNo || !actionType) return
    // 失败时直接抛出：由 ConfirmDialog 统一提示后端原因并保持弹窗打开，成功后关闭
    if (actionType === "refill") await ordersApi.refill(actionTradeNo)
    else await ordersApi.resendNotify(actionTradeNo)
    toast.success(actionType === "refill" ? "补单成功" : "通知发送成功")
    refresh()
    setActionTradeNo(null)
    setActionType(null)
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-9 gap-3 items-end">
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
              inputMode="numeric"
              value={merchantId}
              onChange={(e) => setMerchantId(digitsOnly(e.target.value))}
            />
            <Select value={orderType || "_all"} onValueChange={(v) => setOrderType(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"订单类型"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部类型"}</SelectItem>
                <SelectItem value="0">{"正式收款"}</SelectItem>
                <SelectItem value="1">{"余额充值"}</SelectItem>
                <SelectItem value="2">{"测试单"}</SelectItem>
              </SelectContent>
            </Select>
            <Select value={channelCodeId || "_all"}
                    onValueChange={(v) => setChannelCodeId(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"通道编码"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部编码"}</SelectItem>
                {channelCodes.map((c) => (
                  <SelectItem key={c.id} value={String(c.id)}>
                    {c.name && c.name !== c.code ? `${c.code}（${c.name}）` : c.code}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={statusFilter || "_all"} onValueChange={(v) => setStatusFilter(v === "_all" ? "" : v)}>
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
              <Button size="sm" onClick={() => applyFilterParams()}>
                <IconSearch className="size-4" />
                {"查询"}
              </Button>
              <Button variant="outline" size="sm" onClick={handleReset}>
                <IconRefresh className="size-4" />
                {"重置"}
              </Button>
              {EXPORT_ENABLED && canExport && (
                <Button variant="outline" size="sm" onClick={handleExport}>
                  <IconDownload className="size-4" />
                  {"导出 Excel"}
                </Button>
              )}
            </div>
            {summaryMoney !== "" && (
              <div className="text-sm text-muted-foreground">
                {"筛选结果总金额"}：<span className="font-medium tabular-nums text-foreground">¥{summaryMoney}</span>
              </div>
            )}
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

      {/* 订单详情：两列信息表，一行两组「字段 / 值」，长内容在单元格内换行 */}
      <Dialog open={!!detailOrder} onOpenChange={(open) => !open && setDetailOrder(null)}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"订单详情"}</DialogTitle>
            <DialogDescription className="font-mono text-xs">{detailOrder?.trade_no}</DialogDescription>
          </DialogHeader>
          {detailOrder && <OrderDetailTable order={detailOrder} />}
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

/** 订单详情主体：桌面一行两组「字段 / 值」，窄屏自动退化成一行一组 */
function OrderDetailTable({ order }: { order: OrderData }) {
  const typeBadge = order.order_type === 2
    ? <Badge variant="secondary">{"测试单（不计入资金与统计）"}</Badge>
    : order.order_type === 1
      ? <Badge variant="outline">{"余额充值"}</Badge>
      : null

  return (
    <DetailTable rows={[
      [
        { label: "状态", value: (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <OrderStatusBadge status={order.status} />
            {typeBadge}
          </span>
        ) },
        { label: "商品名称", value: order.subject, wrap: true },
      ],
      [
        { label: "商户", value: order.merchant_username || String(order.merchant_id ?? "") },
        { label: "支付方式", value: order.type_name || order.type,
          icon: <PayIcon code={order.type} icon={order.type_icon} /> },
      ],
      [
        { label: "金额", value: `¥${order.money}` },
        { label: "实付金额", value: order.realmoney ? `¥${order.realmoney}` : "" },
      ],
      [
        { label: "商户到账", value: order.merchant_income ? `¥${order.merchant_income}` : "" },
        { label: "平台利润", value: order.profit ? `¥${order.profit}` : "" },
      ],
      [
        { label: "通道插件", value: order.plugin_name },
        { label: "通道编码", value: order.channel_code || "", mono: true },
      ],
      [
        { label: "上游订单号", value: order.api_trade_no, mono: true, wrap: true },
        { label: "商户订单号", value: order.out_trade_no, mono: true, wrap: true },
      ],
      [
        { label: "买家账号", value: order.buyer_id, wrap: true },
        { label: "客户端 IP", value: order.client_ip, mono: true },
      ],
      [
        { label: "通知次数", value: String(order.notify_count ?? 0) },
        { label: "创建时间", value: order.created_at },
      ],
      [
        { label: "支付时间", value: order.paid_at },
        { label: "过期时间", value: order.expire_at },
      ],
      [
        { label: "回跳地址", value: order.return_url, mono: true, wrap: true },
        { label: "扩展参数", value: order.param, mono: true, wrap: true },
      ],
    ]} />
  )
}
