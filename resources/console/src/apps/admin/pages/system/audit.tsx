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
import { IconSearch, IconRefresh, IconEye, IconUserSearch } from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { digitsOnly, toIntParam } from "@/lib/form-utils"
import { auditActionLabel } from "@/lib/audit-actions"

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

// 已知动作名的中文说明在 `@/lib/audit-actions`（键由后端 record_audit 写入）。
// 未收录的动作直接显示原始 action，后端新增动作不需要改前端就能看见。

const labelOf = (map: Record<string, string> | { value: string; label: string }[], key: string) =>
  Array.isArray(map)
    ? (map.find((o) => o.value === key)?.label ?? (key || "—"))
    : (map[key] ?? (key || "—"))

/** 动作单元格：中文说明 + 原始键（未收录的动作两行相同，只显示一行） */
function ActionCell({ action }: { action: string }) {
  const label = auditActionLabel(action)
  return (
    <div className="flex flex-col">
      <span className="text-sm">{label}</span>
      {label !== action && (
        <span className="font-mono text-xs text-muted-foreground">{action}</span>
      )}
    </div>
  )
}

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
  // 点列表里的「按此操作人筛选」时递增的重放信号：保证重复点同一行也会重新查询
  const [quickActor, setQuickActor] = React.useState<{ guard: string; actorId: number } | null>(null)

  // 「先选人再带 ID」：后端只认 actor_id（数字），所以从行数据里带出 ID + 身份域，
  // 不让人手打姓名（姓名在后端是无结果的模糊值）
  const filterByActor = (row: AuditLogData) => {
    if (!row.actor_id) return
    setGuardFilter(row.actor_guard || "")
    setActorIdFilter(String(row.actor_id))
    setQuickActor({ guard: row.actor_guard || "", actorId: row.actor_id })
  }

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
        cell: ({ row }) => <ActionCell action={row.original.action} />,
        meta: { headClassName: "min-w-[180px]" },
      },
      {
        accessorKey: "actor_name",
        enableSorting: false,
        header: () => "操作人",
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <div className="flex min-w-0 flex-col">
              <span className="truncate text-sm">{row.original.actor_name || "—"}</span>
              <span className="text-xs text-muted-foreground">
                {labelOf(GUARD_OPTIONS, row.original.actor_guard)}
                {row.original.actor_id ? ` #${row.original.actor_id}` : ""}
              </span>
            </div>
            {row.original.actor_id ? (
              <Button
                variant="ghost"
                size="icon-xs"
                className="shrink-0 text-muted-foreground"
                title={"按此操作人筛选"}
                aria-label={`按操作人 ${row.original.actor_name || row.original.actor_id} 筛选`}
                onClick={() => filterByActor(row.original)}
              >
                <IconUserSearch />
              </Button>
            ) : null}
          </div>
        ),
        meta: { headClassName: "min-w-[150px]" },
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

  // 点行内「按此操作人筛选」后，等 setGuardFilter/setActorIdFilter 落到 filtersRef
  // 再发起查询（applyFilters 是同一次渲染之后的稳定回调）
  React.useEffect(() => {
    if (quickActor) applyFilters()
  }, [quickActor, applyFilters])

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
                    {auditActionLabel(action)}
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
              inputMode="numeric"
              aria-label={"操作人 ID（仅数字）"}
              placeholder={"操作人 ID（仅数字）"}
              value={actorIdFilter}
              onChange={(e) => setActorIdFilter(digitsOnly(e.target.value))}
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

          <p className="text-xs leading-relaxed text-muted-foreground">
            {"「操作人」只支持按数字 ID 精确筛选（后端不支持姓名或模糊匹配，输姓名查不到数据）；ID 在不同身份域下会重复，建议同时选「操作人域」，或点列表里操作人旁的按钮一键带入。审计行目前由后台操作与定时任务写入（后台账号 / 系统任务）。"}
          </p>

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
            <DialogDescription className="text-xs">
              {detail && `#${detail.id} · ${auditActionLabel(detail.action)} · ${detail.created_at}`}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
                <span className="text-muted-foreground">{"动作"}</span>
                <span>
                  {auditActionLabel(detail.action)}
                  {auditActionLabel(detail.action) !== detail.action && (
                    <>
                      {" "}
                      <span className="font-mono text-muted-foreground">{detail.action}</span>
                    </>
                  )}
                </span>
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
