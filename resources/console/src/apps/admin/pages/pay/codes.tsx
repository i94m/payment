"use client"

import * as React from "react"
import { useNavigate } from "react-router-dom"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { payApi } from "@/api"
import type {
  ChannelCodeData,
  ChannelCodePayload,
  ChannelOptionData,
  PaymentTypeData,
} from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Field, FieldLabel, FieldDescription, FieldError, RequiredMark } from "@/components/ui/field"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
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
  switchCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { PayTypeLabel } from "@/components/pay-icon"
import { IconPlus, IconEdit, IconTrash, IconList, IconRefresh, IconSearch } from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"

/** 编码级策略：与通道 mode 同口径（0=随机 1=轮询 2=权重） */
const MODE_LABEL: Record<number, string> = { 0: "随机", 1: "轮询", 2: "权重" }

// 行内编辑态：成员勾选/权重
interface MemberDraft {
  channel_id: number
  weight: number
}

// 编码表单校验：与后端 /channel-codes 的校验口径一致（编码格式/至少一个通道/权重模式非全零）
// 放在前端是为了当场给提示，不把用户的重填成本留到提交之后。
const memberSchema = z.object({
  channel_id: z.number(),
  weight: z.number().int("权重必须是整数")
    .min(0, "权重不能为负数")
    .max(1000000, "权重不能超过 1000000"),
})

const codeSchema = z.object({
  code: z.string().trim()
    .min(1, "通道编码不能为空")
    .max(32, "通道编码最多 32 位")
    .regex(/^[a-z0-9_-]+$/i, "只支持字母、数字、- 和 _"),
  name: z.string().trim().max(64, "名称最多 64 个字符"),
  remark: z.string().trim().max(255, "备注最多 255 个字符"),
  mode: z.number(),
  status: z.number(),
  members: z.record(z.string(), memberSchema),
}).superRefine((values, ctx) => {
  const items = Object.values(values.members)
  if (items.length === 0) {
    ctx.addIssue({ code: "custom", path: ["members"], message: "请至少勾选一个通道" })
    return
  }
  if (values.mode === 2 && !items.some((it) => it.weight > 0)) {
    ctx.addIssue({ code: "custom", path: ["members"], message: "权重模式下至少一个通道的权重要大于 0" })
  }
})

type CodeFormValues = z.infer<typeof codeSchema>

const emptyValues = (): CodeFormValues => ({
  code: "", name: "", remark: "", mode: 0, status: 1, members: {},
})

/**
 * 下游通道编码：编码是给下游的对外契约（全局唯一命名空间）。
 * 成员权重与「编码内临时开关」是这套设计的核心 —— 关闭成员只是把这条上游从这个编码里摘掉，
 * 不影响其它编码与默认路由；编码内全关则下游直接报错，绝不回落到编码之外。
 */
export function ChannelCodesPage() {
  const navigate = useNavigate()

  const [channels, setChannels] = React.useState<ChannelOptionData[]>([])
  const [payTypes, setPayTypes] = React.useState<PaymentTypeData[]>([])
  const [statusFilter, setStatusFilter] = React.useState("")

  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [saving, setSaving] = React.useState(false)

  // 表单输入用 z.input（members 由成员表行内编辑写入），提交时拿到校验后的 z.output
  const codeForm = useForm<z.input<typeof codeSchema>, unknown, CodeFormValues>({
    resolver: zodResolver(codeSchema),
    defaultValues: emptyValues(),
  })
  const members = codeForm.watch("members")
  // 权重列只在「权重」模式出现：其它模式下权重不入候选排序（值仍随成员保留，
  // 切回权重模式不用重新填），列表里历史权重也不会被清掉。
  const mode = codeForm.watch("mode")
  const showWeight = mode === 2
  // 「至少一个通道 / 权重全零」是整段的错误，挂在 members 上统一展示
  const membersError = (codeForm.formState.errors.members as { message?: string } | undefined)?.message
  const [deleteId, setDeleteId] = React.useState<number | null>(null)

  React.useEffect(() => {
    // 成员选择需要全量通道与支付方式名称：加载失败显式提示（否则弹窗里是空成员表）。
    // 走 channel-options 而不是通道列表：只要 channel_code.view 就能读，且不含费率/凭证。
    payApi.channelOptions()
      .then(setChannels)
      .catch((err) => toast.error(getErrorMessage(err, "通道列表加载失败")))
    payApi.payTypes()
      .then(setPayTypes)
      .catch((err) => toast.error(getErrorMessage(err, "支付方式加载失败")))
  }, [])

  const typeItem = (id: number) => payTypes.find((p) => p.id === id)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (statusFilter !== "") params.status = Number(statusFilter)
    return params
  }, [statusFilter])

  const toggleStatus = async (item: ChannelCodeData) => {
    const next = item.status === 1 ? 0 : 1
    try {
      await payApi.setChannelCodeStatus(item.id, next)
      updateRow(item.id, { status: next })
      toast.success(next === 1 ? "编码已启用" : "编码已停用")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitDelete = async () => {
    if (!deleteId) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    await payApi.deleteChannelCode(deleteId)
    setDeleteId(null)
    refresh()
    toast.success("删除成功")
  }

  const openCreate = () => {
    setEditingId(null)
    codeForm.reset(emptyValues())
    setDialogOpen(true)
  }

  const openEdit = async (id: number) => {
    try {
      const detail = await payApi.channelCodeDetail(id)
      const memberMap: Record<string, MemberDraft> = {}
      detail.items.forEach((it) => {
        memberMap[String(it.channel_id)] = {
          channel_id: it.channel_id, weight: it.weight,
        }
      })
      setEditingId(id)
      codeForm.reset({
        code: detail.code, name: detail.name, remark: detail.remark,
        mode: detail.mode, status: detail.status, members: memberMap,
      })
      setDialogOpen(true)
    } catch (err) {
      toast.error(getErrorMessage(err, "加载失败"))
    }
  }

  const toggleMember = (channelId: number) => {
    const next = { ...codeForm.getValues("members") }
    if (next[channelId]) delete next[channelId]
    else next[channelId] = { channel_id: channelId, weight: 1 }
    // 提交过一次后再改成员就实时复校验：用户看到「请至少勾选一个通道」后勾上能立刻消掉
    codeForm.setValue("members", next, { shouldDirty: true, shouldValidate: codeForm.formState.submitCount > 0 })
  }

  const patchMember = (channelId: number, patch: Partial<MemberDraft>) => {
    codeForm.setValue("members", {
      ...codeForm.getValues("members"),
      [channelId]: { ...codeForm.getValues("members")[channelId], ...patch },
    }, { shouldDirty: true, shouldValidate: codeForm.formState.submitCount > 0 })
  }

  const submit = async (values: CodeFormValues) => {
    const items = Object.values(values.members)
    const payload: ChannelCodePayload = {
      code: values.code.trim().toLowerCase(),
      name: values.name.trim(),
      remark: values.remark.trim(),
      mode: values.mode,
      status: values.status,
      items: items.map((it, idx) => ({ ...it, sort: idx })),
    }
    setSaving(true)
    try {
      if (editingId) await payApi.updateChannelCode(editingId, payload)
      else await payApi.createChannelCode(payload)
      toast.success(editingId ? "通道编码已更新" : "通道编码已创建")
      setDialogOpen(false)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setSaving(false)
    }
  }

  const {
    table, total, loading, error, keyword, setKeyword, applyFilters, refresh, updateRow,
  } = useDataList<ChannelCodeData>({
    fetchFn: (params) => payApi.channelCodes(params as never),
    filters: filterParams,
    columns: [
      {
        accessorKey: "code",
        enableSorting: false,
        header: () => "通道编码",
        cell: ({ getValue }) => (
          <span className="font-mono text-sm font-medium">{getValue() as string}</span>
        ),
        meta: { headClassName: "w-[110px]", sticky: "left" },
      },
      {
        accessorKey: "name",
        enableSorting: false,
        header: () => "名称",
        cell: ({ row }) => (
          <span className="block max-w-full truncate text-sm" title={row.original.name}>
            {row.original.name || "—"}
          </span>
        ),
        meta: { headClassName: "min-w-[110px]", cellClassName: "max-w-[160px]" },
      },
      {
        accessorKey: "mode",
        enableSorting: false,
        header: () => "策略",
        cell: ({ getValue }) => secondaryCell(MODE_LABEL[getValue() as number] ?? "随机"),
        meta: { headClassName: "w-[70px]" },
      },
      {
        accessorKey: "item_count",
        enableSorting: false,
        header: () => "关联通道",
        cell: ({ row }) => {
          const isWeight = row.original.mode === 2
          // 权重模式下把成员权重挂到悬浮提示：列表一眼能核对「谁占多少比例」
          const tip = (row.original.members ?? [])
            .map((m) => (isWeight ? `${m.channel_name} ×${m.weight}` : m.channel_name))
            .join("\n")
          return (
            <span className="text-sm tabular-nums" title={tip || undefined}>
              {row.original.item_count}
            </span>
          )
        },
        meta: { headClassName: "w-[90px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "today_amount",
        enableSorting: false,
        header: () => "今日收款",
        cell: ({ row }) => secondaryCell(row.original.today_amount ?? "-"),
        meta: { headClassName: "w-[100px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "yesterday_amount",
        enableSorting: false,
        header: () => "昨日收款",
        cell: ({ row }) => secondaryCell(row.original.yesterday_amount ?? "-"),
        meta: { headClassName: "w-[100px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => switchCell(row.original.status === 1, () => toggleStatus(row.original)),
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      actionsColumn([
        { label: "编辑", icon: <IconEdit className="size-4" />, onClick: (item) => openEdit(item.id) },
        {
          label: "删除",
          icon: <IconTrash className="size-4" />,
          variant: "destructive",
          onClick: (item) => setDeleteId(item.id),
        },
        {
          label: "订单",
          icon: <IconList className="size-4" />,
          onClick: (item) => navigate(`/trade/orders?channel_code_id=${item.id}`),
        },
      ] as ActionItem<ChannelCodeData>[], { width: 220 }),
    ],
    perPage: 20,
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Input
                className="h-9 w-[220px]"
                placeholder={"编码 / 名称"}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
              />
              <Select value={statusFilter || "_all"} onValueChange={(v) => setStatusFilter(v === "_all" ? "" : v)}>
                <SelectTrigger className="h-9 w-[140px]">
                  <SelectValue placeholder={"状态"} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="_all">{"全部状态"}</SelectItem>
                  <SelectItem value="1">{"已启用"}</SelectItem>
                  <SelectItem value="0">{"已停用"}</SelectItem>
                </SelectContent>
              </Select>
              <Button size="sm" onClick={applyFilters}>
                <IconSearch className="size-4" />
                {"查询"}
              </Button>
              <Button variant="outline" size="sm" onClick={() => { setStatusFilter(""); setKeyword(""); applyFilters() }}>
                <IconRefresh className="size-4" />
                {"重置"}
              </Button>
            </div>
            <Button size="sm" onClick={openCreate}>
              <IconPlus className="size-4" />
              {"新增通道编码"}
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

      {/* 编码编辑：主档 + 成员（权重 / 是否启用） */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-y-auto"
                       onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>{editingId ? "修改通道编码" : "新增通道编码"}</DialogTitle>
            <DialogDescription>
              {"下游下单时带上这个编码，订单只会走这里勾选的通道，不会跑到别的通道上去。"}
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={codeForm.handleSubmit(submit)} className="space-y-4">
            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              <Controller
                name="code"
                control={codeForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor="code">{"通道编码"}<RequiredMark /></FieldLabel>
                    <Input
                      {...field}
                      id="code"
                      className="font-mono"
                      placeholder={"如 8088"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error
                      ? <FieldError>{fieldState.error.message}</FieldError>
                      : <FieldDescription className="text-xs">{"字母/数字/-/_，最多 32 位，自动转小写"}</FieldDescription>}
                  </Field>
                )}
              />
              <Controller
                name="name"
                control={codeForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor="code-name">{"名称"}</FieldLabel>
                    <Input
                      {...field}
                      id="code-name"
                      placeholder={"如 下游A-支付宝"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="mode"
                control={codeForm.control}
                render={({ field }) => (
                  <Field>
                    <FieldLabel htmlFor="code-mode">{"策略"}</FieldLabel>
                    <Select value={String(field.value)} onValueChange={(v) => field.onChange(Number(v))}>
                      <SelectTrigger id="code-mode" className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="0">{"随机"}</SelectItem>
                        <SelectItem value="1">{"轮询"}</SelectItem>
                        <SelectItem value="2">{"权重"}</SelectItem>
                      </SelectContent>
                    </Select>
                    <FieldDescription className="text-xs">
                      {showWeight
                        ? "按权重比例随机：权重越大越容易被选中，权重 0 的通道不参与分流"
                        : "随机：每单随机选；轮询：依次轮流"}
                    </FieldDescription>
                  </Field>
                )}
              />
              <Controller
                name="status"
                control={codeForm.control}
                render={({ field }) => (
                  <Field>
                    <FieldLabel htmlFor="code-status">{"是否启用"}</FieldLabel>
                    <div className="flex h-9 items-center gap-2">
                      <Switch
                        id="code-status"
                        checked={field.value === 1}
                        onCheckedChange={(v) => field.onChange(v ? 1 : 0)}
                      />
                      <span className="text-sm text-muted-foreground">{field.value === 1 ? "启用" : "停用"}</span>
                    </div>
                    <FieldDescription className="text-xs">
                      {"停用后下游再用这个编码下单会直接报错"}
                    </FieldDescription>
                  </Field>
                )}
              />
            </div>

            <Field>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <FieldLabel>{"关联通道"}<RequiredMark /></FieldLabel>
                <span className="text-xs text-muted-foreground">
                  {showWeight
                    ? "勾选后参与分流；权重越大越容易被选中，权重 0 等于摘流"
                    : "勾选后才会参与这个编码的分流"}
                </span>
              </div>
              <div className={`max-h-[320px] overflow-y-auto rounded-md border${
                membersError ? " border-destructive" : ""
              }`}>
                <table className="w-full table-fixed text-sm">
                  <thead className="sticky top-0 bg-muted/60 text-xs text-muted-foreground">
                    <tr>
                      <th className="w-12 px-2 py-2 text-center">{"绑定"}</th>
                      <th className={showWeight ? "w-[38%] px-2 py-2 text-left" : "w-[46%] px-2 py-2 text-left"}>
                        {"通道"}
                      </th>
                      <th className="px-2 py-2 text-left">{"支付方式"}</th>
                      {showWeight && <th className="w-[24%] px-2 py-2 text-right">{"权重"}</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {channels.length === 0 && (
                      <tr>
                        <td colSpan={showWeight ? 4 : 3} className="px-2 py-6 text-center text-xs text-muted-foreground">
                          {"暂无可用通道，请先到「支付通道」创建"}
                        </td>
                      </tr>
                    )}
                    {channels.map((ch) => {
                      const member = members[ch.id]
                      const pt = typeItem(ch.payment_type_id)
                      return (
                        <tr key={ch.id} className="border-t">
                          <td className="px-2 py-1.5 text-center">
                            <input
                              type="checkbox"
                              className="size-4 cursor-pointer accent-primary"
                              checked={!!member}
                              onChange={() => toggleMember(ch.id)}
                              aria-label={`绑定 ${ch.name}`}
                            />
                          </td>
                          <td className="overflow-hidden px-2 py-1.5">
                            <div className="flex min-w-0 items-center gap-1.5">
                              <span className="min-w-0 truncate" title={ch.name}>{ch.name}</span>
                              {ch.status !== 1 && <Badge variant="outline" className="shrink-0">{"全局停用"}</Badge>}
                            </div>
                            <div className="truncate text-xs text-muted-foreground" title={ch.plugin_name}>
                              {ch.plugin_name}
                            </div>
                          </td>
                          <td className="px-2 py-1.5">
                            <PayTypeLabel code={pt?.code} icon={pt?.icon} name={pt?.name ?? String(ch.payment_type_id)} />
                          </td>
                          {showWeight && (
                            <td className="px-2 py-1.5 text-right">
                              <Input
                                type="number"
                                min={0}
                                max={1000000}
                                step={1}
                                className="h-8 w-16 text-right"
                                value={member ? member.weight : ""}
                                disabled={!member}
                                onChange={(e) => patchMember(ch.id, { weight: Number(e.target.value) || 0 })}
                              />
                            </td>
                          )}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {membersError && <FieldError>{membersError}</FieldError>}
            </Field>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>{"取消"}</Button>
              <Button type="submit" disabled={saving}>{"确定"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!deleteId}
        onOpenChange={(open) => { if (!open) setDeleteId(null) }}
        title={"删除"}
        description={"删除后该编码立即失效（下游再传会报「不存在或不可用」），历史订单保留当时的编码留痕。确认删除？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"删除"}
        onConfirm={submitDelete}
      />
    </div>
  )
}
