"use client"

import * as React from "react"
import { announcesApi } from "@/api"
import type { AnnounceData } from "@/api"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { FilterCard } from "@/components/filter-card"
import { Field, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Badge } from "@/components/ui/badge"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import {
  useDataList,
  ConfirmDialog,
  ListError,
  actionsColumn,
  dateCell,
  truncatedCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconPlus, IconEdit, IconTrash } from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"

// 公告表单：无标题，仅内容（必填）
const announceSchema = z.object({
  content: z.string().trim().min(1, "公告内容不能为空"),
  status: z.number(),
})

export function AnnouncesPage() {
  const [editing, setEditing] = React.useState<AnnounceData | null>(null)
  const [creating, setCreating] = React.useState(false)
  const [deleteTarget, setDeleteTarget] = React.useState<AnnounceData | null>(null)

  const form = useForm<z.infer<typeof announceSchema>>({
    resolver: zodResolver(announceSchema),
    defaultValues: { content: "", status: 1 },
  })

  const openCreate = () => {
    form.reset({ content: "", status: 1 })
    setCreating(true)
  }

  const openEdit = (item: AnnounceData) => {
    setEditing(item)
    form.reset({ content: item.content, status: item.status })
  }

  const submit = form.handleSubmit(async (values) => {
    try {
      if (creating) await announcesApi.create({ content: values.content, status: values.status })
      else if (editing) await announcesApi.update(editing.id, { content: values.content, status: values.status })
      toast.success(creating ? "公告已创建" : "公告已更新")
      setCreating(false)
      setEditing(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  })

  const doDelete = async () => {
    if (!deleteTarget) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    await announcesApi.remove(deleteTarget.id)
    toast.success("删除成功")
    setDeleteTarget(null)
    refresh()
  }

  const {
    table, loading, error, refresh,
  } = useDataList<AnnounceData>({
    fetchFn: (params) => announcesApi.list(params as never),
    columns: [
      {
        accessorKey: "id",
        enableSorting: false,
        header: () => "ID",
        meta: { headClassName: "w-[70px]" },
      },
      {
        accessorKey: "content",
        enableSorting: false,
        header: () => "公告内容",
        cell: ({ row }) => truncatedCell(row.original.content, 420),
        meta: { headClassName: "min-w-[240px]", sticky: "left" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => (
          <Badge variant={row.original.status === 1 ? "default" : "secondary"}>
            {row.original.status === 1 ? "发布中" : "下线"}
          </Badge>
        ),
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
        { label: "编辑", icon: <IconEdit className="size-4" />, onClick: (item) => openEdit(item) },
        {
          label: "删除",
          icon: <IconTrash className="size-4" />,
          variant: "destructive",
          onClick: (item) => setDeleteTarget(item),
        },
      ] as ActionItem<AnnounceData>[], { width: 160 }),
    ],
    perPage: 20,
  })

  const formOpen = creating || !!editing

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard className="flex-row justify-end">
          <Button size="sm" onClick={openCreate}>
            <IconPlus className="size-4" />
            {"新建公告"}
          </Button>
        </FilterCard>

        <div className="px-4 lg:px-6">
          {error ? (
            <ListError error={error} onRetry={refresh} />
          ) : (
            <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
              <DataTablePagination table={table} />
            </DataTable>
          )}
        </div>
      </div>

      {/* 新建/编辑表单 */}
      {formOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => { setCreating(false); setEditing(null) }}>
          <div className="w-full max-w-lg rounded-xl bg-card p-6 shadow-xl m-4" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold mb-4">{creating ? "新建公告" : "编辑"}</h3>
            <form className="space-y-3" onSubmit={submit}>
              <Controller
                name="content"
                control={form.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"公告内容"}<RequiredMark /></FieldLabel>
                    <Textarea id={field.name} rows={5} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={"请输入公告内容，将展示在商户端用户中心顶部"} aria-invalid={fieldState.invalid || undefined} />
                    {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="status"
                control={form.control}
                render={({ field }) => (
                  // 复选框：官方横排模式，控件在前、标签在后
                  <Field orientation="horizontal">
                    <input id={field.name} type="checkbox" checked={field.value === 1} onChange={(e) => field.onChange(e.target.checked ? 1 : 0)} />
                    <FieldLabel htmlFor={field.name} className="font-normal">{"发布中"}</FieldLabel>
                  </Field>
                )}
              />
              <div className="flex justify-end gap-2 pt-4">
                <Button variant="outline" type="button" onClick={() => { setCreating(false); setEditing(null) }}>{"取消"}</Button>
                <Button type="submit">{"保存设置"}</Button>
              </div>
            </form>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        title={"删除"}
        description={"删除后商户端将不再展示该公告，确定删除？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={doDelete}
      />
    </div>
  )
}
