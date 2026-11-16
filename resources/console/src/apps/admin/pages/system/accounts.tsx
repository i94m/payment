"use client"

import * as React from "react"
import { permissionApi } from "@/api"
import type { AdminAccountData, PermissionNode } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Badge } from "@/components/ui/badge"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  ConfirmDialog,
  ListError,
  actionsColumn,
  secondaryCell,
  dateCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconPlus, IconEdit, IconTrash } from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"

// 账号表单 schema：编辑时密码留空 = 不修改，仅新增时必填（权限树勾选为非文本字段，不进 schema）
const accountSchema = (isEdit: boolean) =>
  z.object({
    username: z.string().trim().min(1, "用户名不能为空"),
    name: z.string(),
    password: isEdit ? z.string() : z.string().min(1, "密码不能为空"),
    isForbid: z.boolean(),
  })

export function AccountsPage() {

  const [statusFilter, setStatusFilter] = React.useState("")
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [deleteId, setDeleteId] = React.useState<number | null>(null)
  const [tree, setTree] = React.useState<PermissionNode[]>([])

  // 表单（权限树勾选保留独立 state）
  const [permIds, setPermIds] = React.useState<Set<number>>(new Set())
  const form = useForm<z.infer<ReturnType<typeof accountSchema>>>({
    resolver: zodResolver(accountSchema(!!editingId)),
    defaultValues: { username: "", name: "", password: "", isForbid: false },
  })

  React.useEffect(() => {
    permissionApi.tree()
      .then(setTree)
      .catch((err) => toast.error(getErrorMessage(err, "权限树加载失败，保存前请刷新重试")))
  }, [])

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (statusFilter !== "") params.status = statusFilter
    return params
  }, [statusFilter])

  const handleReset = () => {
    setKeyword("")
    setStatusFilter("")
    applyFilters()
  }

  const togglePerm = (id: number, checked: boolean | string) => {
    setPermIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const toggleGroup = (node: PermissionNode, checked: boolean) => {
    setPermIds((prev) => {
      const next = new Set(prev)
      const ids = [node.id, ...(node.children || []).map((c) => c.id)]
      ids.forEach((id) => checked ? next.add(id) : next.delete(id))
      return next
    })
  }

  const openCreate = () => {
    setEditingId(null)
    form.reset({ username: "", name: "", password: "", isForbid: false })
    setPermIds(new Set())
    setDialogOpen(true)
  }

  const openEdit = async (id: number) => {
    try {
      const detail = await permissionApi.accountDetail(id)
      setEditingId(id)
      form.reset({ username: detail.username, name: detail.name || "", password: "", isForbid: !!detail.is_forbid })
      setPermIds(new Set(detail.permissions || []))
      setDialogOpen(true)
    } catch (err) {
      toast.error(getErrorMessage(err, "加载失败"))
    }
  }

  const submit = form.handleSubmit(async (values) => {
    setSaving(true)
    try {
      const payload = {
        username: values.username.trim(),
        name: values.name.trim(),
        password: values.password,
        is_forbid: values.isForbid,
        permissions: [...permIds],
      }
      if (editingId) await permissionApi.updateAccount(editingId, payload)
      else await permissionApi.createAccount(payload)
      toast.success(editingId ? "账号已更新" : "账号已创建")
      setDialogOpen(false)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setSaving(false)
    }
  })

  const submitDelete = async () => {
    if (!deleteId) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    await permissionApi.deleteAccount(deleteId)
    setDeleteId(null)
    refresh()
    toast.success("删除成功")
  }

  const {
    table, total, loading, error, keyword, setKeyword, applyFilters, refresh,
  } = useDataList<AdminAccountData>({
    fetchFn: (params) => permissionApi.accounts(params as never),
    filters: filterParams,
    columns: [
      {
        accessorKey: "id",
        enableSorting: false,
        header: () => "ID",
        cell: ({ getValue }) => secondaryCell(getValue() as number),
        meta: { headClassName: "w-[60px]", sticky: "left" },
      },
      {
        accessorKey: "username",
        enableSorting: false,
        header: () => "用户名",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{row.original.username}</span>
            {row.original.is_super_admin && <Badge variant="outline" className="text-[10px]">{"超级管理员"}</Badge>}
          </div>
        ),
        meta: { headClassName: "min-w-[130px]" },
      },
      {
        accessorKey: "name",
        enableSorting: false,
        header: () => "姓名",
        cell: ({ row }) => secondaryCell(row.original.name || "—"),
        meta: { headClassName: "min-w-[100px]" },
      },
      {
        accessorKey: "permissions",
        enableSorting: false,
        header: () => "权限",
        cell: ({ row }) => {
          if (row.original.is_super_admin) return <span className="text-xs text-muted-foreground">* </span>
          const count = row.original.permission_details?.length ?? 0
          return (
            <div className="flex flex-wrap gap-1 max-w-[260px]">
              {(row.original.permission_details || []).map((p) => (
                <Badge key={p.id} variant="secondary" className="text-[10px]">{p.name}</Badge>
              ))}
              {count === 0 && <span className="text-xs text-muted-foreground">—</span>}
            </div>
          )
        },
        meta: { headClassName: "min-w-[200px]" },
      },
      {
        accessorKey: "is_forbid",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => (
          <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${row.original.is_forbid ? "bg-destructive/10 text-destructive" : "bg-emerald-500/10 text-emerald-600"}`}>
            {row.original.is_forbid ? "禁用" : "正常"}
          </span>
        ),
        meta: { headClassName: "w-[70px]" },
      },
      {
        accessorKey: "created_at",
        enableSorting: false,
        header: () => "创建时间",
        cell: ({ row }) => dateCell(row.original.created_at),
        meta: { headClassName: "min-w-[150px]" },
      },
      actionsColumn([
        { label: "编辑", icon: <IconEdit className="size-4" />, onClick: (item) => openEdit(item.id) },
        {
          label: "删除",
          icon: <IconTrash className="size-4" />,
          variant: "destructive",
          onClick: (item) => setDeleteId(item.id),
        },
      ] as ActionItem<AdminAccountData>[], { width: 160 }),
    ],
    perPage: 20,
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 items-end max-w-xl">
            <div className="col-span-1">
              <Input
                placeholder={"搜索用户名/姓名..."}
                className="h-9"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
              />
            </div>
            <Select value={statusFilter || "_all"} onValueChange={(v) => setStatusFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"状态"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部状态"}</SelectItem>
                <SelectItem value="normal">{"正常"}</SelectItem>
                <SelectItem value="forbid">{"禁用"}</SelectItem>
              </SelectContent>
            </Select>
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
            </div>
            <Button size="sm" onClick={openCreate}>
              <IconPlus className="size-4" />
              {"新增账号"}
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

      {/* 新建/编辑账号 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? "编辑账号" : "新增账号"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={submit} className="grid gap-6">
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <Controller
                  name="username"
                  control={form.control}
                  render={({ field, fieldState }) => (
                    <Field data-invalid={fieldState.invalid}>
                      <FieldLabel htmlFor={field.name}>{"用户名"}<RequiredMark /></FieldLabel>
                      <Input id={field.name} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} disabled={!!editingId} placeholder={"请输入登录账号"} aria-invalid={fieldState.invalid || undefined} />
                      {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                    </Field>
                  )}
                />
                <Controller
                  name="name"
                  control={form.control}
                  render={({ field }) => (
                    <Field>
                      <FieldLabel htmlFor={field.name}>{"姓名"}</FieldLabel>
                      <Input id={field.name} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={"请输入显示名称"} />
                    </Field>
                  )}
                />
                <Controller
                  name="password"
                  control={form.control}
                  render={({ field, fieldState }) => (
                    <Field data-invalid={fieldState.invalid} className="col-span-2">
                      <FieldLabel htmlFor={field.name}>{"密码"}{!editingId && <RequiredMark />}</FieldLabel>
                      <Input
                        id={field.name}
                        type="password"
                        value={field.value ?? ""}
                        onChange={(e) => field.onChange(e.target.value)}
                        placeholder={editingId ? "创建时必填；编辑时留空表示不修改" : "请输入密码"}
                        aria-invalid={fieldState.invalid || undefined}
                      />
                      {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                    </Field>
                  )}
                />
              </div>

              <Controller
                name="isForbid"
                control={form.control}
                render={({ field }) => (
                  // 复选框：官方横排模式，控件在前、标签在后，保留原边框行样式
                  <Field orientation="horizontal" className="rounded-lg border px-3 py-2.5">
                    <Checkbox id={field.name} checked={!!field.value} onCheckedChange={(v) => field.onChange(!!v)} />
                    <FieldLabel htmlFor={field.name} className="font-normal">{"禁用"}</FieldLabel>
                  </Field>
                )}
              />

              {/* 权限树 */}
              <div className="space-y-2 rounded-lg border p-3">
                <p className="text-sm font-medium">{"权限"}</p>
                {tree.map((node) => {
                  const groupChecked = permIds.has(node.id) || (node.children || []).every((c) => permIds.has(c.id)) && (node.children || []).length > 0
                  const groupIndeterminate = (node.children || []).some((c) => permIds.has(c.id)) && !(node.children || []).every((c) => permIds.has(c.id))
                  return (
                    <div key={node.id} className="space-y-1.5">
                      <div className="flex items-center gap-2">
                        <Checkbox
                          checked={groupIndeterminate ? "indeterminate" : groupChecked}
                          onCheckedChange={(v) => toggleGroup(node, !!v)}
                        />
                        <span className="text-sm font-medium">{node.name}</span>
                      </div>
                      {(node.children || []).length > 0 && (
                        <div className="flex flex-wrap gap-x-4 gap-y-1.5 pl-6">
                          {(node.children || []).map((c) => (
                            <label key={c.id} className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                              <Checkbox
                                checked={permIds.has(c.id)}
                                onCheckedChange={(v) => togglePerm(c.id, v ?? false)}
                              />
                              {c.name}
                            </label>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" type="button" onClick={() => setDialogOpen(false)}>{"取消"}</Button>
              <Button type="submit" disabled={saving}>{"保存设置"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!deleteId}
        onOpenChange={(open) => { if (!open) setDeleteId(null) }}
        title={"删除"}
        description={"确定删除该管理员账号？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"删除"}
        onConfirm={submitDelete}
      />
    </div>
  )
}
