"use client"

import * as React from "react"
import { auditApi } from "@/api"
import type { AuditLogData } from "@/api"
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
  dateCell,
  secondaryCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconEye } from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { toIntParam } from "@/lib/form-utils"

// 操作人身份域：与后端 AuditResult.guard 对齐
const GUARD_OPTIONS: { value: string; label: string }[] = [
  { value: "admin", label: "后台账号" },
  { value: "merchant", label: "商户" },
  { value: "agent", label: "代理" },
  { value: "system", label: "系统任务" },
]

// 被操作对象类型：与后端 record_audit 的 target_type 对齐
const TARGET_OPTIONS: { value: string; label: string }[] = [
  { value: "merchant", label: "商户" },
  { value: "channel", label: "支付通道" },
  { value: "plugin", label: "支付插件" },
  { value: "order", label: "订单" },
  { value: "settlement", label: "结算单" },
  { value: "setting", label: "站点配置" },
]

// 已知动作名的中文说明；未收录的（如 merchant.status_*）直接显示原始 action，
// 后端新增动作不需要改前端就能看见
const ACTION_LABELS: Record<string, string> = {
  "merchant.update": "修改商户资料",
  "merchant.delete": "删除商户",
  "merchant.password_reset": "重置商户密码",
  "merchant.balance_adjust": "调整商户余额",
  "merchant.status_enable": "启用商户",
  "merchant.status_disable": "停用商户",
  "channel.create": "新增支付通道",
  "channel.update": "修改支付通道",
  "channel.delete": "删除支付通道",
  "plugin.install": "安装支付插件",
  "order.refill": "订单手动补单",
  "setting.update": "修改站点配置",
  "settle.pay": "结算打款",
  "settle.reject": "驳回结算",
}

const labelOf = (map: Record<string, string> | { value: string; label: string }[], key: string) =>
  Array.isArray(map)
    ? (map.find((o) => o.value === key)?.label ?? (key || "—"))
    : (map[key] ?? (key || "—"))

/** 前后值快照：只读 JSON 展示，空对象/空值统一显示破折号 */
function Snapshot({ title, data }: { title: string; data: Record<string, unknown> | null }) {
  const empty = !data || Object.keys(data).length === 0
  return (
    <div className="min-w-0">
      <p className="mb-1 text-xs text-muted-foreground">{title}</p>
      {empty ? (
        <p className="rounded-lg bg-muted p-3 text-xs text-muted-foreground">{"—"}</p>
      ) : (
        <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted p-3 font-mono text-xs">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function AuditPage() {

  const [actionFilter, setActionFilter] = React.useState("")
  const [guardFilter, setGuardFilter] = React.useState("")
  const [actorIdFilter, setActorIdFilter] = React.useState("")
  const [targetTypeFilter, setTargetTypeFilter] = React.useState("")
  const [targetIdFilter, setTargetIdFilter] = React.useState("")
  const [startFilter, setStartFilter] = React.useState("")
  const [endFilter, setEndFilter] = React.useState("")
  const [actions, setActions] = React.useState<string[]>([])
  const [detail, setDetail] = React.useState<AuditLogData | null>(null)

  // 动作下拉数据源；加载失败只提示、不影响默认「全部动作」可用
  React.useEffect(() => {
    auditApi.actions().then(setActions)
      .catch((err) => toast.error(getErrorMessage(err, "动作列表加载失败")))
  }, [])

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (actionFilter !== "") params.action = actionFilter
    if (guardFilter !== "") params.guard = guardFilter
    const actorId = toIntParam(actorIdFilter)
    if (actorId !== undefined) params.actor_id = String(actorId)
    if (targetTypeFilter !== "") params.target_type = targetTypeFilter
    if (targetIdFilter.trim() !== "") params.target_id = targetIdFilter.trim()
    if (startFilter !== "") params.start = startFilter
    if (endFilter !== "") params.end = endFilter
    return params
  }, [actionFilter, guardFilter, actorIdFilter, targetTypeFilter, targetIdFilter, startFilter, endFilter])

  const handleReset = () => {
    setActionFilter("")
    setGuardFilter("")
    setActorIdFilter("")
    setTargetTypeFilter("")
    setTargetIdFilter("")
    setStartFilter("")
    setEndFilter("")
    applyFilters()
  }

  const {
    table, total, loading, error, refresh, applyFilters,
  } = useDataList<AuditLogData>({
    // 审计查询没有关键字概念，过滤项全部走 filters
    fetchFn: (params) => auditApi.list(params),
    filters: filterParams,
    columns: [
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]", sticky: "left" },
      },
      {
        accessorKey: "action",
        enableSorting: false,
        header: () => "动作",
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="text-sm">{ACTION_LABELS[row.original.action] ?? row.original.action}</span>
            <span className="font-mono text-xs text-muted-foreground">{row.original.action}</span>
          </div>
        ),
        meta: { headClassName: "min-w-[180px]" },
      },
      {
        accessorKey: "actor_name",
        enableSorting: false,
        header: () => "操作人",
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="text-sm">{row.original.actor_name || "—"}</span>
            <span className="text-xs text-muted-foreground">
              {labelOf(GUARD_OPTIONS, row.original.actor_guard)}
              {row.original.actor_id ? ` #${row.original.actor_id}` : ""}
            </span>
          </div>
        ),
        meta: { headClassName: "min-w-[140px]" },
      },
      {
        accessorKey: "target_type",
        enableSorting: false,
        header: () => "对象",
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="text-sm">{labelOf(TARGET_OPTIONS, row.original.target_type)}</span>
            <span className="font-mono text-xs text-muted-foreground">{row.original.target_id || "—"}</span>
          </div>
        ),
        meta: { headClassName: "min-w-[130px]" },
      },
      {
        accessorKey: "ip",
        enableSorting: false,
        header: () => "IP",
        cell: ({ row }) => secondaryCell(row.original.ip || "—"),
        meta: { headClassName: "w-[130px]" },
      },
      actionsColumn([
        {
          label: "详情",
          icon: <IconEye className="size-4" />,
          onClick: (item) => setDetail(item),
        },
      ] as ActionItem<AuditLogData>[], { width: 100 }),
    ],
    perPage: 20,
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 gap-3 items-end sm:grid-cols-3 lg:grid-cols-4">
            <Select value={actionFilter || "_all"} onValueChange={(v) => setActionFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"动作"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部动作"}</SelectItem>
                {actions.map((action) => (
                  <SelectItem key={action} value={action}>
                    {ACTION_LABELS[action] ?? action}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={guardFilter || "_all"} onValueChange={(v) => setGuardFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"操作人域"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部操作人"}</SelectItem>
                {GUARD_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              className="h-9"
              placeholder={"操作人 ID"}
              value={actorIdFilter}
              onChange={(e) => setActorIdFilter(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
            />
            <Select value={targetTypeFilter || "_all"} onValueChange={(v) => setTargetTypeFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"对象类型"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部对象"}</SelectItem>
                {TARGET_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              className="h-9"
              placeholder={"对象 ID / 订单号"}
              value={targetIdFilter}
              onChange={(e) => setTargetIdFilter(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
            />
            <Input
              className="h-9"
              type="date"
              aria-label={"开始日期"}
              value={startFilter}
              onChange={(e) => setStartFilter(e.target.value)}
            />
            <Input
              className="h-9"
              type="date"
              aria-label={"结束日期"}
              value={endFilter}
              onChange={(e) => setEndFilter(e.target.value)}
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
            <DataTable table={table} loading={loading} emptyMessage={"暂无审计记录"}>
              <DataTablePagination table={table} total={total} />
            </DataTable>
          )}
        </div>
      </div>

      <Dialog open={!!detail} onOpenChange={(open) => !open && setDetail(null)}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"审计详情"}</DialogTitle>
            <DialogDescription className="font-mono text-xs">
              {detail && `#${detail.id} · ${detail.action} · ${detail.created_at}`}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
                <span className="text-muted-foreground">{"操作人"}</span>
                <span>
                  {detail.actor_name || "—"}
                  {" "}
                  <span className="text-muted-foreground">
                    {`（${labelOf(GUARD_OPTIONS, detail.actor_guard)}${detail.actor_id ? ` #${detail.actor_id}` : ""}）`}
                  </span>
                </span>
                <span className="text-muted-foreground">{"对象"}</span>
                <span className="font-mono">
                  {`${labelOf(TARGET_OPTIONS, detail.target_type)} / ${detail.target_id || "—"}`}
                </span>
                <span className="text-muted-foreground">{"IP"}</span>
                <span className="font-mono">{detail.ip || "—"}</span>
                <span className="text-muted-foreground">{"备注"}</span>
                <span>{detail.remark || "—"}</span>
                <span className="text-muted-foreground">{"User-Agent"}</span>
                <span className="break-all font-mono">{detail.user_agent || "—"}</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <Snapshot title={"变更前"} data={detail.before} />
                <Snapshot title={"变更后"} data={detail.after} />
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  )
}
