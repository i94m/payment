"use client"

import * as React from "react"
import { agentsApi } from "@/api"
import type { AgentData } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { ConfirmDialog } from "@/components/data-list/confirm-dialog"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  actionsColumn,
  dateCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import {
  IconSearch, IconRefresh, IconPlus, IconEdit, IconLogin,
  IconCopy, IconTrash,
} from "@tabler/icons-react"
import { toast } from "sonner"

// 名称必填 ≤64；密码长度在提交时按新增/编辑分别校验（编辑可留空不改）
const agentSchema = z.object({
  name: z.string().trim().min(1, "代理名称不能为空").max(64, "代理名称不超过 64 字"),
  password: z.string(),
  remark: z.string().max(255, "备注不超过 255 字"),
})

type AgentFormValues = z.infer<typeof agentSchema>

export function AgentsPage() {
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [editing, setEditing] = React.useState<AgentData | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [deleteTarget, setDeleteTarget] = React.useState<AgentData | null>(null)
  const { copyToClipboard } = useCopyToClipboard()

  const form = useForm<AgentFormValues>({
    resolver: zodResolver(agentSchema),
    defaultValues: { name: "", password: "", remark: "" },
  })

  const openCreate = () => {
    setEditing(null)
    form.reset({ name: "", password: "", remark: "" })
    setDialogOpen(true)
  }

  const openEdit = (item: AgentData) => {
    setEditing(item)
    form.reset({ name: item.name, password: "", remark: item.remark || "" })
    setDialogOpen(true)
  }

  const submit = form.handleSubmit(async (values) => {
    // 密码：新增必填 ≥6 位；编辑留空不改（对齐老系统）
    const pwd = values.password
    if ((!editing && pwd.length < 6) || (editing && pwd && pwd.length < 6)) {
      form.setError("password", { message: "代理密码至少 6 位" })
      return
    }
    setSaving(true)
    try {
      if (editing) {
        await agentsApi.update(editing.id, {
          name: values.name.trim(),
          password: pwd || undefined,
          remark: values.remark,
        })
      } else {
        await agentsApi.create({
          name: values.name.trim(),
          password: pwd,
          remark: values.remark,
        })
      }
      toast.success(editing ? "代理已更新" : "代理已创建")
      setDialogOpen(false)
      refresh()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setSaving(false)
    }
  })

  const doDelete = async () => {
    if (!deleteTarget) return
    try {
      await agentsApi.remove(deleteTarget.id)
      toast.success("删除成功")
      setDeleteTarget(null)
      refresh()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  const doSso = async (item: AgentData) => {
    try {
      const res = await agentsApi.sso(item.id)
      window.open(res.url, "_blank")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  const doCopyLink = (item: AgentData) => {
    const full = (typeof window !== "undefined" ? window.location.origin : "") + item.link
    copyToClipboard(full)
    toast.success("复制成功")
  }

  const {
    table, total, loading, keyword, setKeyword, applyFilters, refresh,
  } = useDataList<AgentData>({
    // keyword 搜索框映射为后端的 kw（名称/备注模糊）
    fetchFn: (params) => {
      const { keyword: kw, ...rest } = params as { keyword?: string }
      return agentsApi.list({ ...rest, kw: kw || "" })
    },
    columns: [
      {
        accessorKey: "id",
        enableSorting: false,
        header: () => "ID",
        cell: ({ getValue }) => <span className="text-sm tabular-nums">{getValue() as number}</span>,
        meta: { headClassName: "w-[70px]" },
      },
      {
        accessorKey: "name",
        enableSorting: false,
        header: () => "代理名称",
        cell: ({ row }) => <span className="text-sm font-medium">{row.original.name}</span>,
        meta: { headClassName: "min-w-[120px]" },
      },
      {
        accessorKey: "remark",
        enableSorting: false,
        header: () => "备注",
        cell: ({ row }) => truncatedCell(row.original.remark, 200),
        meta: { headClassName: "min-w-[140px]" },
      },
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "创建时间",
        cell: ({ row }) => dateCell(row.original.created_at, "—"),
        meta: { headClassName: "min-w-[150px]" },
      },
      {
        accessorKey: "last_login_at",
        enableSorting: false,
        header: () => "最后登录",
        cell: ({ row }) => dateCell(row.original.last_login_at, "—"),
        meta: { headClassName: "min-w-[150px]" },
      },
      actionsColumn([
        {
          label: "编辑",
          icon: <IconEdit className="size-4" />,
          onClick: (item) => openEdit(item),
        },
        {
          label: "登录",
          icon: <IconLogin className="size-4" />,
          onClick: (item) => doSso(item),
        },
        {
          label: "复制链接",
          icon: <IconCopy className="size-4" />,
          onClick: (item) => doCopyLink(item),
        },
        {
          label: "删除",
          icon: <IconTrash className="size-4" />,
          variant: "destructive",
          onClick: (item) => setDeleteTarget(item),
        },
      ] as ActionItem<AgentData>[], { width: 340 }),
    ],
    perPage: 15,
    onError: () => toast.error("操作失败"),
  })

  const handleReset = () => {
    setKeyword("")
    applyFilters()
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 items-end">
            <Input
              placeholder={"搜索代理名称或备注"}
              className="h-9"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
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
            <Button size="sm" className="ml-auto" onClick={openCreate}>
              <IconPlus className="size-4" />
              {"新增代理"}
            </Button>
          </div>
        </FilterCard>

        <div className="px-4 lg:px-6">
          <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
            <DataTablePagination table={table} total={total} />
          </DataTable>
        </div>
      </div>

      {/* 新增/编辑代理弹窗（对齐老系统：编辑时密码留空则不修改） */}
      <Dialog open={dialogOpen} onOpenChange={(open) => { if (!open) setDialogOpen(false) }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editing ? "编辑代理" : "新增代理"}</DialogTitle>
            {editing && (
              <DialogDescription>
                {`ID ${editing.id} · 创建于 ${editing.created_at || "—"}`}
              </DialogDescription>
            )}
          </DialogHeader>
          <form onSubmit={submit} className="space-y-4">
            <Controller
              name="name"
              control={form.control}
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="agent-name">{"代理名称"}<RequiredMark /></FieldLabel>
                  <Input
                    id="agent-name"
                    value={field.value ?? ""}
                    onChange={(e) => field.onChange(e.target.value)}
                    maxLength={64}
                    placeholder={"代理登录账号"}
                    autoComplete="new-password"
                    aria-invalid={fieldState.invalid || undefined}
                  />
                  {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                </Field>
              )}
            />
            <Controller
              name="password"
              control={form.control}
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="agent-password">
                    {"登录密码"}
                    {!editing && <RequiredMark />}
                  </FieldLabel>
                  <Input
                    id="agent-password"
                    type="password"
                    value={field.value ?? ""}
                    onChange={(e) => field.onChange(e.target.value)}
                    placeholder={editing ? "至少 6 位" : "至少 6 位"}
                    autoComplete="new-password"
                    aria-invalid={fieldState.invalid || undefined}
                  />
                  {editing && !fieldState.error && (
                    <span className="text-xs text-muted-foreground">{"留空则不修改密码"}</span>
                  )}
                  {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                </Field>
              )}
            />
            <Controller
              name="remark"
              control={form.control}
              render={({ field, fieldState }) => (
                <Field data-invalid={fieldState.invalid}>
                  <FieldLabel htmlFor="agent-remark">{"备注"}</FieldLabel>
                  <Input
                    id="agent-remark"
                    value={field.value ?? ""}
                    onChange={(e) => field.onChange(e.target.value)}
                    maxLength={255}
                    placeholder={"备注信息"}
                    aria-invalid={fieldState.invalid || undefined}
                  />
                  {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                </Field>
              )}
            />
          </form>
          <DialogFooter>
            <Button variant="outline" type="button" onClick={() => setDialogOpen(false)}>
              {"取消"}
            </Button>
            <Button type="button" disabled={saving} onClick={() => submit()}>
              {saving ? "加载中..." : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        title={"删除"}
        description={`${"确认删除该代理？其历史订单归属保留"}（${deleteTarget?.name}）`}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={doDelete}
      />
    </div>
  )
}
