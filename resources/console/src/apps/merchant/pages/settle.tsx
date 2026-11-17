"use client"

import * as React from "react"
import { Link } from "react-router-dom"
import { settleApi, recordsApi } from "@/api"
import type { MerchantSettlementData, SettleInfoExtended } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { dateCell, secondaryCell, truncatedCell, ListError, PanelError } from "@/components/data-list/"
import { useReactTable, getCoreRowModel, type ColumnDef } from "@tanstack/react-table"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { numStr } from "@/lib/form-schema"
import { getErrorMessage } from "@/lib/error"
import { IconWallet, IconClock, IconSnowflake, IconSend, IconChevronRight } from "@tabler/icons-react"
import { toast } from "sonner"

// 申请结算表单
const applySchema = z.object({
  amount: numStr("申请金额", { min: 0.01 }),
})

// 结算方式文案（与修改资料页一致；此处仅展示，维护入口在「修改资料」）
const SETTLE_TYPE_LABELS: Record<number, string> = {
  1: "支付宝",
  2: "微信",
  3: "QQ",
  4: "银行卡",
}

export function SettlePage() {
  const [info, setInfo] = React.useState<SettleInfoExtended | null>(null)
  const [infoLoading, setInfoLoading] = React.useState(true)
  const [infoError, setInfoError] = React.useState<string | null>(null)
  const [applying, setApplying] = React.useState(false)

  // 申请结算表单
  const applyForm = useForm<z.input<typeof applySchema>, unknown, z.output<typeof applySchema>>({
    resolver: zodResolver(applySchema),
    defaultValues: { amount: "" },
  })
  // 手续费试算需要实时读取申请金额
  const amount = applyForm.watch("amount")

  const [data, setData] = React.useState<MerchantSettlementData[]>([])
  const [total, setTotal] = React.useState(0)
  const [page, setPage] = React.useState(1)
  const [loading, setLoading] = React.useState(true)
  const [listError, setListError] = React.useState<string | null>(null)
  const [perPage, setPerPage] = React.useState(10)
  // 申请成功后强制刷新列表（page 已是 1 时 setPage 不会触发 effect）
  const [listTick, setListTick] = React.useState(0)

  const loadInfo = React.useCallback(() => {
    settleApi.info()
      .then((res) => {
        setInfo(res as SettleInfoExtended)
        setInfoError(null)
      })
      .catch((err) => setInfoError(getErrorMessage(err, "结算信息加载失败")))
      .finally(() => setInfoLoading(false))
  }, [])

  React.useEffect(() => { loadInfo() }, [loadInfo])

  React.useEffect(() => {
    setLoading(true)
    setListError(null)
    recordsApi.settlements({ page, per_page: perPage })
      .then((res) => { setData(res.items); setTotal(res.total) })
      .catch((err) => setListError(getErrorMessage(err, "结算记录加载失败")))
      .finally(() => setLoading(false))
  }, [page, perPage, listTick])

  const feePreview = React.useMemo(() => {
    if (!info || !(amount ?? "").trim()) return null
    const a = Number(amount)
    if (isNaN(a) || a <= 0) return null
    const rate = Number(info.fee_rate || 0)
    let fee = (a * rate) / 100
    // 保底/封顶与后端口径一致
    const feeMin = Number(info.fee_min || 0)
    const feeMax = Number(info.fee_max || 0)
    if (feeMin > 0 && fee < feeMin) fee = feeMin
    if (feeMax > 0 && fee > feeMax) fee = feeMax
    // 手续费不低于申请金额时实打金额无意义（后端会拒绝）：前端先给出明确提示
    if (fee >= a) return { fee: fee.toFixed(2), real: "0.00", invalid: true }
    return { fee: fee.toFixed(2), real: (a - fee).toFixed(2), invalid: false }
  }, [info, amount])

  const apply = applyForm.handleSubmit(async (values) => {
    // 先按页面展示的可提余额做一次软校验：并发/口径变化仍以后端为准
    const withdrawable = Number(info?.withdrawable ?? NaN)
    if (!Number.isNaN(withdrawable) && Number(values.amount) > withdrawable) {
      applyForm.setError("amount", { message: `申请金额不能超过可提余额 ¥${info?.withdrawable}` })
      return
    }
    setApplying(true)
    try {
      await settleApi.apply(values.amount.trim())
      toast.success("结算申请已提交")
      applyForm.reset()
      loadInfo()
      setPage(1)
      setListTick((n) => n + 1)
    } catch (err) {
      toast.error(getErrorMessage(err, "结算申请提交失败"))
    } finally {
      setApplying(false)
    }
  })

  const statusBadge = (status: number) => {
    const map: Record<number, { label: string; cls: string }> = {
      0: { label: "待处理", cls: "bg-secondary text-secondary-foreground" },
      1: { label: "已打款", cls: "bg-primary/10 text-primary" },
      2: { label: "已拒绝", cls: "bg-destructive/10 text-destructive" },
    }
    const it = map[status]
    return it ? (
      <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${it.cls}`}>{it.label}</span>
    ) : String(status)
  }

  const columns = React.useMemo<ColumnDef<MerchantSettlementData>[]>(() => [
    { accessorKey: "settle_no", enableSorting: false, header: () => "结算单号", cell: ({ row }) => truncatedCell(row.original.settle_no, 140), meta: { headClassName: "min-w-[150px]" } },
    { accessorKey: "amount", enableSorting: false, header: () => "结算金额", cell: ({ row }) => <span className="text-sm font-medium tabular-nums">¥{row.original.amount}</span> },
    { accessorKey: "fee", enableSorting: false, header: () => "手续费", cell: ({ row }) => secondaryCell(row.original.fee) },
    { accessorKey: "real_amount", enableSorting: false, header: () => "实打金额", cell: ({ row }) => <span className="text-sm tabular-nums">¥{row.original.real_amount}</span> },
    { accessorKey: "account", enableSorting: false, header: () => "收款账号", cell: ({ row }) => truncatedCell(row.original.account || "—", 120) },
    { accessorKey: "status", enableSorting: false, header: () => "状态", cell: ({ row }) => statusBadge(row.original.status) },
    { accessorKey: "created_at", enableSorting: false, header: () => "创建时间", cell: ({ row }) => dateCell(row.original.created_at), meta: { headClassName: "min-w-[150px]" } },
    { accessorKey: "paid_at", enableSorting: false, header: () => "支付时间", cell: ({ row }) => dateCell(row.original.paid_at || null, "—"), meta: { headClassName: "min-w-[150px]" } },
  ], []) // eslint-disable-line react-hooks/exhaustive-deps

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    pageCount: Math.ceil(total / perPage) || 1,
    state: { pagination: { pageIndex: page - 1, pageSize: perPage } },
    // 手动分页必须回写 data 的 page/perPage，否则翻页按钮点了不生效
    onPaginationChange: (updater) => {
      const prev = { pageIndex: page - 1, pageSize: perPage }
      const next = typeof updater === "function" ? updater(prev) : updater
      setPerPage(next.pageSize)
      setPage(next.pageIndex + 1)
    },
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        {/* 余额概览 + 申请 */}
        <div className="grid gap-4 px-4 lg:px-6 lg:grid-cols-3">
          <Card className="border-0 shadow-sm lg:col-span-2">
            <CardHeader>
              <CardTitle className="text-base">{"结算中心"}</CardTitle>
            </CardHeader>
            <CardContent>
              {info ? (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-primary/10 p-2.5"><IconWallet className="size-5 text-primary" /></div>
                    <div>
                      <p className="text-xs text-muted-foreground">{"可结算余额"}</p>
                      <p className="text-lg font-semibold tabular-nums">¥{info.balance}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-muted p-2.5"><IconSnowflake className="size-5 text-muted-foreground" /></div>
                    <div>
                      <p className="text-xs text-muted-foreground">{"冻结金额"}</p>
                      <p className="text-lg font-semibold tabular-nums">¥{info.frozen_balance}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-muted p-2.5"><IconClock className="size-5 text-muted-foreground" /></div>
                    <div>
                      <p className="text-xs text-muted-foreground">{"待处理结算单"}</p>
                      <p className="text-lg font-semibold tabular-nums">{info.pending_count}</p>
                    </div>
                  </div>
                </div>
              ) : infoError ? (
                <PanelError error={infoError} onRetry={() => { setInfoLoading(true); loadInfo() }} />
              ) : infoLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : null}
              {/* 规则信息：对齐网格 */}
              {info && (
                <div className="mt-6 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
                  {[
                    { label: "结算费率", value: `${info.fee_rate}%` },
                    ...(Number(info.fee_min) > 0 ? [{ label: "手续费保底", value: `¥${info.fee_min}` }] : []),
                    ...(Number(info.fee_max) > 0 ? [{ label: "手续费封顶", value: `¥${info.fee_max}` }] : []),
                    { label: "起结金额", value: `¥${info.min_amount}` },
                    { label: "预留余额", value: `¥${info.remain_money}` },
                    ...(info.d1_enable ? [{ label: "D+1 当日收款", value: `¥${info.today_income} 暂不可提` }] : []),
                    ...(info.daily_limit > 0 ? [{ label: "今日次数", value: `${info.daily_used}/${info.daily_limit}` }] : []),
                  ].map((it) => (
                    <div key={it.label} className="flex items-baseline justify-between gap-2 text-xs">
                      <span className="shrink-0 text-muted-foreground">{it.label}</span>
                      <span className="font-medium tabular-nums">{it.value}</span>
                    </div>
                  ))}
                </div>
              )}
              {/* 结算流程说明 */}
              <div className="mt-6 border-t pt-5">
                <p className="text-sm font-medium text-muted-foreground">{"结算流程"}</p>
                <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
                  {[
                    { step: 1, title: "提交申请", desc: "余额转入冻结，等待平台处理" },
                    { step: 2, title: "平台审核", desc: "驳回则全额退回余额" },
                    { step: 3, title: "打款到账", desc: "扣除手续费后到账" },
                  ].map((it, i, arr) => (
                    <React.Fragment key={it.step}>
                      <div className="flex flex-1 items-start gap-2.5">
                        <span className="mt-px flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">{it.step}</span>
                        <div>
                          <p className="text-xs font-medium">{it.title}</p>
                          <p className="mt-0.5 text-xs text-muted-foreground">{it.desc}</p>
                        </div>
                      </div>
                      {i < arr.length - 1 && <IconChevronRight className="hidden size-4 shrink-0 self-center text-muted-foreground sm:block" />}
                    </React.Fragment>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="border-0 shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="text-base">{"申请结算"}</CardTitle>
              <Link to="/user-center" className="text-xs text-primary underline-offset-2 hover:underline">{"修改收款账号"}</Link>
            </CardHeader>
            <CardContent>
              {/* 收款信息：在「修改资料」维护，此处只读展示 */}
              <div className="rounded-lg bg-muted/50 p-3">
                <div className="grid gap-1.5">
                  <p className="text-sm">
                    <span className="text-muted-foreground">{"结算方式"}：</span>
                    <span className="font-medium">{info?.settle_type_name || SETTLE_TYPE_LABELS[Number(info?.settle_type ?? 1)] || "—"}</span>
                  </p>
                  <p className="text-sm break-all">
                    <span className="text-muted-foreground">{"结算账号"}：</span>
                    <span className="font-medium">{info?.settle_account || "—"}</span>
                  </p>
                  <p className="text-sm">
                    <span className="text-muted-foreground">{"真实姓名"}：</span>
                    <span className="font-medium">{info?.settle_name || "—"}</span>
                  </p>
                </div>
              </div>

              <form onSubmit={apply} className="mt-4 space-y-3">
                <Controller
                  name="amount"
                  control={applyForm.control}
                  render={({ field, fieldState }) => (
                    <Field data-invalid={fieldState.invalid}>
                      <div className="flex items-center justify-between">
                        <FieldLabel htmlFor={field.name}>{"申请金额"}<RequiredMark /></FieldLabel>
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {"可提余额"}: ¥{info?.withdrawable ?? "—"}
                          <button type="button" className="ml-1.5 text-primary underline-offset-2 hover:underline"
                            onClick={() => field.onChange(info?.withdrawable ?? "")}>{"全部"}</button>
                        </span>
                      </div>
                      <Input
                        id={field.name}
                        inputMode="decimal"
                        placeholder={"申请金额"}
                        value={field.value ?? ""}
                        onChange={(e) => field.onChange(e.target.value)}
                        aria-invalid={fieldState.invalid || undefined}
                      />
                      {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                    </Field>
                  )}
                />
                {feePreview && (
                  feePreview.invalid ? (
                    <p className="text-xs text-destructive">{"手续费不低于申请金额，请提高申请金额后再提交"}</p>
                  ) : (
                    <p className="text-xs text-muted-foreground">
                      {"手续费"}: ¥{feePreview.fee} · {"实打金额"}: <b>¥{feePreview.real}</b>
                    </p>
                  )
                )}
                <Button type="submit" className="w-full" disabled={applying || !info}>
                  <IconSend className="size-4" />
                  {applying ? "提交中…" : "申请结算"}
                </Button>
              </form>
            </CardContent>
          </Card>
        </div>

        {/* 结算记录 */}
        <div className="px-4 lg:px-6">
          <Card className="border-0 shadow-sm">
            <CardHeader>
              <CardTitle className="text-base">{"结算记录"}</CardTitle>
            </CardHeader>
            <CardContent>
              {listError ? (
                <ListError error={listError} onRetry={() => { setListTick((n) => n + 1) }} />
              ) : (
                <>
                  <DataTable table={table} loading={loading} emptyMessage={"暂无结算记录"} />
                  <DataTablePagination table={table} total={total} />
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
