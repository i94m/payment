"use client"

import * as React from "react"
import { settleApi } from "@/api"
import type { AdminSettleData } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  ConfirmDialog,
  ListError,
  actionsColumn,
  secondaryCell,
  dateCell,
  copyableCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem, StatusBadgeOption } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconCheck, IconBan } from "@tabler/icons-react"
import { toast } from "sonner"
import { digitsOnly, toIntParam } from "@/lib/form-utils"
import { useSearchParams } from "react-router-dom"

const settleStatusOptions: StatusBadgeOption[] = [
  { label: "待处理", value: 0, variant: "secondary" },
  { label: "已打款", value: 1, variant: "default" },
  { label: "已拒绝", value: 2, variant: "destructive" },
]

export function SettlePage() {
  // 支持从商户列表快捷跳转：/settle?merchant_id=xx
  const [searchParams] = useSearchParams()

  const [statusFilter, setStatusFilter] = React.useState("")
  const [merchantId, setMerchantId] = React.useState(searchParams.get("merchant_id") || "")
  const [actionTarget, setActionTarget] = React.useState<{ item: AdminSettleData; type: "pay" | "reject" } | null>(null)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (statusFilter !== "") params.status = Number(statusFilter)
    const mid = toIntParam(merchantId)
    if (mid !== undefined) params.merchant_id = mid
    return params
  }, [statusFilter, merchantId])

  const handleReset = () => {
    setStatusFilter("")
    setMerchantId("")
    applyFilters()
  }

  const submitAction = async () => {
    if (!actionTarget) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    if (actionTarget.type === "pay") await settleApi.markPaid(actionTarget.item.id)
    else await settleApi.reject(actionTarget.item.id)
    toast.success(actionTarget.type === "pay" ? "已确认打款" : "已驳回")
    setActionTarget(null)
    refresh()
  }

  const {
    table, total, loading, error, applyFilters, refresh,
  } = useDataList<AdminSettleData>({
    fetchFn: (params) => settleApi.adminList(params as never),
    filters: filterParams,
    columns: [
      {
        accessorKey: "settle_no",
        enableSorting: false,
        header: () => "结算单号",
        cell: ({ row }) => copyableCell(row.original.settle_no),
        meta: { headClassName: "min-w-[160px]", sticky: "left" },
      },
      {
        accessorKey: "merchant_username",
        enableSorting: false,
        header: () => "商户",
        cell: ({ row }) => secondaryCell(row.original.merchant_username || row.original.merchant_id),
        meta: { headClassName: "min-w-[90px]" },
      },
      {
        accessorKey: "amount",
        enableSorting: false,
        header: () => "结算金额",
        cell: ({ row }) => <span className="text-sm font-medium tabular-nums">¥{row.original.amount}</span>,
        meta: { headClassName: "w-[100px]" },
      },
      {
        accessorKey: "fee",
        enableSorting: false,
        header: () => "手续费",
        cell: ({ row }) => secondaryCell(row.original.fee),
        meta: { headClassName: "w-[80px]" },
      },
      {
        accessorKey: "real_amount",
        enableSorting: false,
        header: () => "实打金额",
        cell: ({ row }) => <span className="text-sm tabular-nums">¥{row.original.real_amount}</span>,
        meta: { headClassName: "w-[100px]" },
      },
      {
        accessorKey: "account",
        enableSorting: false,
        header: () => "收款账号",
        cell: ({ row }) => truncatedCell(row.original.account, 120),
        meta: { headClassName: "min-w-[120px]" },
      },
      {
        accessorKey: "account_name",
        enableSorting: false,
        header: () => "收款人",
        cell: ({ row }) => secondaryCell(row.original.account_name || "—"),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => {
          const opt = settleStatusOptions.find((o) => o.value === row.original.status)
          const variantCls: Record<string, string> = {
            default: "bg-primary/10 text-primary",
            secondary: "bg-secondary text-secondary-foreground",
            destructive: "bg-destructive/10 text-destructive",
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
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "创建时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]" },
      },
      actionsColumn([
        {
          label: "标记打款",
          icon: <IconCheck className="size-4" />,
          onClick: (item) => setActionTarget({ item, type: "pay" }),
        },
        {
          label: "拒绝",
          icon: <IconBan className="size-4" />,
          variant: "destructive",
          onClick: (item) => setActionTarget({ item, type: "reject" }),
        },
      ] as ActionItem<AdminSettleData>[], { width: 190 }),
    ],
    perPage: 20,
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 items-end max-w-2xl">
            <Select value={statusFilter || "_all"} onValueChange={(v) => setStatusFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"状态"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部状态"}</SelectItem>
                {settleStatusOptions.map((o) => (
                  <SelectItem key={String(o.value)} value={String(o.value)}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              placeholder={"商户 ID"}
              className="h-9"
              inputMode="numeric"
              value={merchantId}
              onChange={(e) => setMerchantId(digitsOnly(e.target.value))}
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

      <ConfirmDialog
        open={!!actionTarget}
        onOpenChange={(open) => { if (!open) setActionTarget(null) }}
        title={actionTarget?.type === "pay" ? "标记打款" : "拒绝"}
        description={
          (actionTarget?.type === "pay" ? "确认已向该账户打款？" : "拒绝后将退回商户余额，确定继续？")
          + (actionTarget ? `（${actionTarget.item.settle_no} / ¥${actionTarget.item.real_amount}）` : "")
        }
        variant={actionTarget?.type === "reject" ? "danger" : "warning"}
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={submitAction}
      />
    </div>
  )
}
