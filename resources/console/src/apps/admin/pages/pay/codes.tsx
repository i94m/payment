"use client"

import * as React from "react"
import { useNavigate } from "react-router-dom"
import { payApi } from "@/api"
import type {
  ChannelCodeData,
  ChannelCodePayload,
  ChannelData,
  PaymentTypeData,
} from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { Textarea } from "@/components/ui/textarea"
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

// 行内编辑态：成员勾选/权重/本编码内开关
interface MemberDraft {
  channel_id: number
  weight: number
  status: number
}

interface CodeDraft {
  code: string
  name: string
  remark: string
  mode: number
  status: number
  members: Record<number, MemberDraft>
}

const emptyDraft = (): CodeDraft => ({
  code: "", name: "", remark: "", mode: 0, status: 1, members: {},
})

/**
 * 下游通道编码：编码是给下游的对外契约（全局唯一命名空间）。
 * 成员权重与「编码内临时开关」是这套设计的核心 —— 关闭成员只是把这条上游从这个编码里摘掉，
 * 不影响其它编码与默认路由；编码内全关则下游直接报错，绝不回落到编码之外。
 */
export function ChannelCodesPage() {
  const navigate = useNavigate()

  const [channels, setChannels] = React.useState<ChannelData[]>([])
  const [payTypes, setPayTypes] = React.useState<PaymentTypeData[]>([])
  const [statusFilter, setStatusFilter] = React.useState("")

  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [draft, setDraft] = React.useState<CodeDraft>(emptyDraft)
  const [saving, setSaving] = React.useState(false)
  const [deleteId, setDeleteId] = React.useState<number | null>(null)

  React.useEffect(() => {
    // 成员选择需要全量通道与支付方式名称：加载失败显式提示（否则弹窗里是空成员表）
    payApi.channels({ page: 1, per_page: 100 })
      .then((res) => setChannels(res.items))
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
    setDraft(emptyDraft())
    setDialogOpen(true)
  }

  const openEdit = async (id: number) => {
    try {
      const detail = await payApi.channelCodeDetail(id)
      const members: Record<number, MemberDraft> = {}
      detail.items.forEach((it) => {
        members[it.channel_id] = {
          channel_id: it.channel_id, weight: it.weight, status: it.status,
        }
      })
      setEditingId(id)
      setDraft({
        code: detail.code, name: detail.name, remark: detail.remark,
        mode: detail.mode, status: detail.status, members,
      })
      setDialogOpen(true)
    } catch (err) {
      toast.error(getErrorMessage(err, "加载失败"))
    }
  }

  const toggleMember = (channelId: number) => {
    setDraft((d) => {
      const members = { ...d.members }
      if (members[channelId]) delete members[channelId]
      else members[channelId] = { channel_id: channelId, weight: 1, status: 1 }
      return { ...d, members }
    })
  }

  const patchMember = (channelId: number, patch: Partial<MemberDraft>) => {
    setDraft((d) => ({
      ...d,
      members: { ...d.members, [channelId]: { ...d.members[channelId], ...patch } },
    }))
  }

  const submit = async () => {
    const items = Object.values(draft.members)
    if (!draft.code.trim()) {
      toast.error("通道编码不能为空")
      return
    }
    if (items.length === 0) {
      toast.error("至少绑定一个通道")
      return
    }
    if (draft.mode === 2 && !items.some((it) => it.weight > 0)) {
      toast.error("权重模式下至少一个通道的权重要大于 0")
      return
    }
    const payload: ChannelCodePayload = {
      code: draft.code.trim().toLowerCase(),
      name: draft.name.trim(),
      remark: draft.remark.trim(),
      mode: draft.mode,
      status: draft.status,
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
        cell: ({ row }) => (
          <span className="text-sm tabular-nums">
            {row.original.active_item_count}
            <span className="text-muted-foreground">/{row.original.item_count}</span>
          </span>
        ),
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

      {/* 编码编辑：主档 + 成员（权重 / 本编码内开关） */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? "修改通道编码" : "新增通道编码"}</DialogTitle>
            <DialogDescription>
              {"编码是给下游的对外契约：下游下单传 channel_code，只能在该编码绑定的通道内分流，绝不回落到编码之外"}
            </DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-3">
            <div className="grid gap-2">
              <Label htmlFor="code">{"通道编码"}</Label>
              <Input
                id="code"
                className="font-mono"
                value={draft.code}
                placeholder={"如 8088"}
                onChange={(e) => setDraft((d) => ({ ...d, code: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">{"字母/数字/-/_，最多 32 位，全局唯一（自动转小写）"}</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="code-name">{"名称"}</Label>
              <Input
                id="code-name"
                value={draft.name}
                placeholder={"如 下游A-支付宝"}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              />
            </div>
            <div className="grid gap-2">
              <Label>{"策略"}</Label>
              <Select
                value={String(draft.mode)}
                onValueChange={(v) => setDraft((d) => ({ ...d, mode: Number(v) }))}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="0">{"随机"}</SelectItem>
                  <SelectItem value="1">{"轮询"}</SelectItem>
                  <SelectItem value="2">{"权重"}</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-end gap-2 pb-2">
              <Switch
                id="code-status"
                checked={draft.status === 1}
                onCheckedChange={(v) => setDraft((d) => ({ ...d, status: v ? 1 : 0 }))}
              />
              <Label htmlFor="code-status" className="cursor-pointer">{"启用"}</Label>
            </div>
            <div className="col-span-2 grid gap-2">
              <Label htmlFor="code-remark">{"备注"}</Label>
              <Textarea
                id="code-remark"
                rows={2}
                value={draft.remark}
                placeholder={"对应哪个下游 / 哪个产品线，排障用"}
                onChange={(e) => setDraft((d) => ({ ...d, remark: e.target.value }))}
              />
            </div>
          </div>

          <div className="mt-2 space-y-2">
            <div className="flex items-center justify-between">
              <Label>{"关联通道"}</Label>
              <span className="text-xs text-muted-foreground">
                {"权重 0 = 不参与抽签；本编码内开关只影响这条通道在这个编码里的分流"}
              </span>
            </div>
            <div className="max-h-[320px] overflow-y-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-muted/60 text-xs text-muted-foreground">
                  <tr>
                    <th className="w-12 px-2 py-2 text-center">{"绑定"}</th>
                    <th className="px-2 py-2 text-left">{"通道"}</th>
                    <th className="w-24 px-2 py-2 text-left">{"支付方式"}</th>
                    <th className="w-20 px-2 py-2 text-right">{"权重"}</th>
                    <th className="w-20 px-2 py-2 text-center">{"本编码"}</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-2 py-6 text-center text-xs text-muted-foreground">
                        {"暂无可用通道，请先到「支付通道」创建"}
                      </td>
                    </tr>
                  )}
                  {channels.map((ch) => {
                    const member = draft.members[ch.id]
                    const pt = typeItem(ch.payment_type_id)
                    return (
                      <tr key={ch.id} className="border-t">
                        <td className="px-2 py-1.5 text-center">
                          <input
                            type="checkbox"
                            className="size-4 accent-primary"
                            checked={!!member}
                            onChange={() => toggleMember(ch.id)}
                            aria-label={`绑定 ${ch.name}`}
                          />
                        </td>
                        <td className="px-2 py-1.5">
                          <span className="flex items-center gap-1.5">
                            <span className="truncate" title={ch.name}>{ch.name}</span>
                            {ch.status !== 1 && <Badge variant="outline">{"全局停用"}</Badge>}
                          </span>
                          <span className="text-xs text-muted-foreground">{ch.plugin_name}</span>
                        </td>
                        <td className="px-2 py-1.5">
                          <PayTypeLabel code={pt?.code} icon={pt?.icon} name={pt?.name ?? String(ch.payment_type_id)} />
                        </td>
                        <td className="px-2 py-1.5 text-right">
                          <Input
                            type="number"
                            min={0}
                            step={1}
                            className="h-8 w-16 text-right"
                            value={member?.weight ?? 1}
                            disabled={!member}
                            onChange={(e) => patchMember(ch.id, { weight: Number(e.target.value) || 0 })}
                          />
                        </td>
                        <td className="px-2 py-1.5 text-center">
                          <Switch
                            checked={member?.status === 1}
                            disabled={!member}
                            onCheckedChange={(v) => patchMember(ch.id, { status: v ? 1 : 0 })}
                          />
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>{"取消"}</Button>
            <Button onClick={submit} disabled={saving}>{"确定"}</Button>
          </DialogFooter>
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
