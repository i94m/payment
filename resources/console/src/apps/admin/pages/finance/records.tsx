"use client"

import * as React from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { adminRecordsApi } from "@/api"
import type { AdminRecordData } from "@/api"
import { format } from "date-fns"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { DatePicker } from "@/components/ui/date-picker"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  ListError,
  secondaryCell,
  dateCell,
  truncatedCell,
} from "@/components/data-list/"
import { IconSearch, IconRefresh, IconDownload } from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { downloadBlob } from "@/lib/export"
import { EXPORT_ENABLED } from "@/lib/flags"
import { digitsOnly, toIntParam } from "@/lib/form-utils"
import apiClient from "@/lib/api-client"
import { usePermission } from "@/hooks/use-permission"

/** 搜索字段（search_field 参数） */
const searchFieldOptions = [
  { value: "", label: "搜索字段" },
  { value: "biz_type", label: "操作类型" },
  { value: "amount", label: "变更金额" },
  { value: "order_trade_no", label: "关联订单号" },
]

/** 操作类型（与后端 BIZ_TYPES 对齐） */
const bizTypeOptions = [
  { value: "order_income", label: "订单收入" },
  { value: "recharge", label: "余额充值" },
  { value: "settle", label: "结算扣款" },
  { value: "settle_reject", label: "结算退回" },
  { value: "adjust", label: "调整" },
  { value: "deposit_in", label: "保证金转入" }, // 保证金功能已下线，历史流水仍需可筛
  { value: "deposit_out", label: "保证金退回" },
  { value: "groupbuy", label: "购买用户组" },
  { value: "refund", label: "退款" }, // 退款功能已下线，历史流水仍需可筛
  { value: "invite_rebate", label: "邀请返现" },
]

export function AdminRecordsPage() {
  const navigate = useNavigate()
  const hasPermission = usePermission()
  // 支持从商户列表快捷跳转：/records?merchant_id=xx（默认选中该商户）
  const [searchParams] = useSearchParams()

  const [searchField, setSearchField] = React.useState("")
  const [merchantId, setMerchantId] = React.useState(searchParams.get("merchant_id") || "")
  const [bizType, setBizType] = React.useState("")
  const [startDate, setStartDate] = React.useState<Date | undefined>(undefined)
  const [endDate, setEndDate] = React.useState<Date | undefined>(undefined)

  const [summary, setSummary] = React.useState<{ inc: string; dec: string; total: string } | null>(null)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    const mid = toIntParam(merchantId)
    if (mid !== undefined) params.merchant_id = mid
    if (bizType) params.biz_type = bizType
    if (searchField) params.search_field = searchField
    if (startDate && endDate) {
      params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
    }
    return params
  }, [merchantId, bizType, searchField, startDate, endDate])

  // 日期区间是成对参数：只选一端会被后端静默忽略，这里显式提示
  const applyFilterParams = () => {
    if (!!startDate !== !!endDate) {
      toast.warning("请同时选择开始日期与结束日期")
      return
    }
    applyFilters()
  }

  const handleReset = () => {
    setSearchField("")
    setMerchantId("")
    setBizType("")
    setStartDate(undefined)
    setEndDate(undefined)
    applyFilters()
  }

  const {
    table, total, loading, error, keyword, setKeyword, applyFilters, refresh,
  } = useDataList<AdminRecordData>({
    fetchFn: async (params) => {
      const { keyword: kw, ...rest } = params as { keyword?: string }
      const res = await adminRecordsApi.list({ ...rest, keyword: kw || "" } as never)
      setSummary((res.summary as unknown as { inc: string; dec: string; total: string }) ?? null)
      return res
    },
    filters: filterParams,
    perPage: 20,
    columns: [
      {
        // 商户号 + 用户名
        accessorKey: "merchant_pid",
        enableSorting: false,
        header: () => "商户",
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="text-sm font-bold tabular-nums">{row.original.merchant_pid}</span>
            <span className="text-xs text-muted-foreground">{row.original.merchant_username}</span>
          </div>
        ),
        meta: { headClassName: "w-[100px]", sticky: "left" },
      },
      {
        // 操作类型（收入绿 / 支出红，对齐旧版 record.php）
        accessorKey: "biz_type_name",
        enableSorting: false,
        header: () => "操作类型",
        cell: ({ row }) => (
          <span className={`text-sm ${Number(row.original.amount) >= 0 ? "text-emerald-600" : "text-rose-500"}`}>
            {row.original.biz_type_name}
          </span>
        ),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "amount",
        enableSorting: false,
        header: () => "变更金额",
        cell: ({ row }) => {
          const v = Number(row.original.amount)
          return (
            <span className={`text-sm font-medium tabular-nums ${v >= 0 ? "text-emerald-600" : "text-rose-500"}`}>
              {v >= 0 ? "+" : ""}{v.toFixed(2)}
            </span>
          )
        },
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "balance_before",
        enableSorting: false,
        header: () => "变更前金额",
        cell: ({ row }) => secondaryCell(`¥${row.original.balance_before}`),
        meta: { headClassName: "w-[95px]" },
      },
      {
        accessorKey: "balance_after",
        enableSorting: false,
        header: () => "变更后金额",
        cell: ({ row }) => secondaryCell(`¥${row.original.balance_after}`),
        meta: { headClassName: "w-[95px]" },
      },
      {
        // 关联订单号（点击跳订单列表按单号搜索）
        accessorKey: "order_trade_no",
        enableSorting: false,
        header: () => "关联订单号",
        cell: ({ row }) => row.original.order_trade_no ? (
          <button
            type="button"
            className="text-left font-mono text-xs text-blue-600 hover:underline"
            onClick={() => navigate(`/trade/orders?trade_no=${row.original.order_trade_no}`)}
          >
            {row.original.order_trade_no}
          </button>
        ) : secondaryCell("—"),
        meta: { headClassName: "min-w-[150px]" },
      },
      {
        accessorKey: "remark",
        enableSorting: false,
        header: () => "备注",
        cell: ({ row }) => truncatedCell(row.original.remark || "—", 120),
        meta: { headClassName: "min-w-[110px]" },
      },
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]" },
      },
    ],
  })

  const handleExport = async () => {
    try {
      const params: Record<string, string> = {}
      const mid = toIntParam(merchantId)
      if (mid !== undefined) params.merchant_id = String(mid)
      if (bizType) params.biz_type = bizType
      if (searchField) params.search_field = searchField
      if (keyword.trim()) params.keyword = keyword.trim()
      if (startDate && endDate) {
        params.addtime = `${format(startDate, "yyyy-MM-dd")},${format(endDate, "yyyy-MM-dd")}`
      }
      const res = await apiClient.get(adminRecordsApi.exportUrl, { params, responseType: "blob" })
      downloadBlob(res as unknown as Blob, `records_${Date.now()}.xlsx`)
    } catch (err) {
      toast.error(getErrorMessage(err, "导出失败"))
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
            <Select value={searchField || "_all"} onValueChange={(v) => setSearchField(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {searchFieldOptions.map((o) => (
                  <SelectItem key={o.value || "_all"} value={o.value || "_all"}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="col-span-1">
              <Input
                placeholder={"搜索内容"}
                className="h-9"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
              />
            </div>
            <Input
              placeholder={"商户 ID"}
              className="h-9"
              inputMode="numeric"
              value={merchantId}
              onChange={(e) => setMerchantId(digitsOnly(e.target.value))}
            />
            <Select value={bizType || "_all"} onValueChange={(v) => setBizType(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部类型"}</SelectItem>
                {bizTypeOptions.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
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

          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => applyFilterParams()}>
                <IconSearch className="size-4" />
                {"查询"}
              </Button>
              <Button variant="outline" size="sm" onClick={handleReset}>
                <IconRefresh className="size-4" />
                {"重置"}
              </Button>
              {EXPORT_ENABLED && hasPermission("merchant.export") && (
                <Button variant="outline" size="sm" onClick={handleExport}>
                  <IconDownload className="size-4" />
                  {"导出 Excel"}
                </Button>
              )}
            </div>
            {summary && (
              <div className="flex items-center gap-4 text-sm text-muted-foreground flex-wrap">
                <span>
                  {"增加金额"}：<span className="font-medium tabular-nums text-emerald-600">¥{summary.inc}</span>
                </span>
                <span>
                  {"减少金额"}：<span className="font-medium tabular-nums text-rose-500">¥{summary.dec}</span>
                </span>
                <span>
                  {"总计金额"}：<span className="font-medium tabular-nums text-foreground">¥{summary.total}</span>
                </span>
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
    </div>
  )
}
