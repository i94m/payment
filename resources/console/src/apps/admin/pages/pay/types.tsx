"use client"

import * as React from "react"
import { useNavigate } from "react-router-dom"
import { payApi } from "@/api"
import type { PaymentTypeData, PaymentTypeStats } from "@/api"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  FormDialog,
  ConfirmDialog,
  actionsColumn,
  secondaryCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconPlus, IconEdit, IconTrash, IconList } from "@tabler/icons-react"
import { z } from "zod"
import { toast } from "sonner"

// 支持设备：0=PC+Mobile 1=PC 2=Mobile
const deviceOptions = [
  { label: "PC+Mobile", value: 0 },
  { label: "PC", value: 1 },
  { label: "Mobile", value: 2 },
]
const deviceLabel = (v: number) => deviceOptions.find((o) => o.value === v)?.label ?? "PC+Mobile"

const typeSchema = z.object({
  // 调用值仅限英文数字，与支付文档一致（^[a-zA-Z0-9]+$）
  code: z.string().min(1).regex(/^[a-zA-Z0-9]+$/, "调用值仅限英文与数字"),
  name: z.string().min(1),
  device: z.coerce.number(),
})

export function PayTypesPage() {

  const navigate = useNavigate()
  const [formOpen, setFormOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [deleteId, setDeleteId] = React.useState<number | null>(null)
  // 今日/昨日收款统计（键为支付方式 id 字符串）
  const [stats, setStats] = React.useState<Record<string, PaymentTypeStats>>({})

  const fetchStats = React.useCallback(() => {
    payApi.typeStats().then(setStats).catch(() => {})
  }, [])

  React.useEffect(() => {
    fetchStats()
  }, [fetchStats])

  const toggleStatus = async (item: PaymentTypeData) => {
    try {
      await payApi.setPayTypeStatus(item.id, item.status === 1 ? 0 : 1)
      updateRow(item.id, { status: item.status === 1 ? 0 : 1 })
      toast.success("状态已更新")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  const submitDelete = async () => {
    if (!deleteId) return
    try {
      await payApi.deletePayType(deleteId)
      setDeleteId(null)
      refresh()
      toast.success("删除成功")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  const {
    table, loading, refresh, updateRow,
  } = useDataList<PaymentTypeData>({
    // 支付方式为全量列表（后端无分页）
    fetchFn: async (params) => {
      const items = await payApi.payTypes()
      return {
        items,
        total: items.length,
        total_page: 1,
        count: items.length,
        per_page: params.per_page,
        current_page: 1,
        has_more_pages: false,
        has_pages: false,
      }
    },
    columns: [
      {
        accessorKey: "code",
        enableSorting: false,
        header: () => "调用值",
        cell: ({ getValue }) => <span className="font-mono text-xs">{getValue() as string}</span>,
        meta: { headClassName: "min-w-[110px]", sticky: "left" },
      },
      {
        accessorKey: "name",
        enableSorting: false,
        // 「名称」列：/assets/icon/{调用值}.ico 图标 + 显示名称，图标缺失时隐藏
        header: () => "名称",
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5 text-sm font-medium">
            <img
              src={`/assets/icon/${row.original.code}.ico`}
              alt=""
              className="h-[18px] w-[18px]"
              onError={(e) => { e.currentTarget.style.display = "none" }}
            />
            {row.original.name}
          </span>
        ),
        meta: { headClassName: "min-w-[130px]" },
      },
      {
        accessorKey: "device",
        enableSorting: false,
        header: () => "支持设备",
        cell: ({ getValue }) => secondaryCell(deviceLabel(getValue() as number)),
        meta: { headClassName: "min-w-[100px]" },
      },
      {
        accessorKey: "today_amount",
        enableSorting: false,
        header: () => "今日收款",
        cell: ({ row }) => secondaryCell(stats[String(row.original.id)]?.today_amount ?? "-"),
        meta: { headClassName: "w-[100px]" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <Switch checked={row.original.status === 1} onCheckedChange={() => toggleStatus(row.original)} />
            <span className="text-xs text-muted-foreground">
              {row.original.status === 1 ? "已开启" : "已关闭"}
            </span>
          </div>
        ),
        meta: { headClassName: "min-w-[100px]" },
      },
      actionsColumn([
        { label: "编辑", icon: <IconEdit className="size-4" />, onClick: (item) => { setEditingId(item.id); setFormOpen(true) } },
        {
          label: "删除",
          icon: <IconTrash className="size-4" />,
          variant: "destructive",
          onClick: (item) => setDeleteId(item.id),
        },
        // 快捷跳转：按调用值过滤订单列表
        { label: "订单", icon: <IconList className="size-4" />, onClick: (item) => navigate(`/trade/orders?type=${item.code}`) },
      ] as ActionItem<PaymentTypeData>[], { width: 230 }),
    ],
    perPage: 100,
    onError: () => toast.error("操作失败"),
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="flex items-center justify-end">
            <Button size="sm" onClick={() => { setEditingId(null); setFormOpen(true) }}>
              <IconPlus className="size-4" />
              {"新增支付方式"}
            </Button>
          </div>
        </FilterCard>

        <div className="px-4 lg:px-6">
          <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
            <DataTablePagination table={table} />
          </DataTable>
        </div>
      </div>

      <FormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editingId={editingId}
        schema={typeSchema}
        // 表单字段：调用值/显示名称/支持设备
        fields={[
          { type: "input", name: "code", label: "调用值", placeholder: "仅限英文，要与支付文档一致", span: "half", required: true },
          { type: "input", name: "name", label: "显示名称", placeholder: "仅显示使用", span: "half", required: true },
          {
            type: "select", name: "device", label: "支持设备", span: "half",
            options: deviceOptions,
          },
        ]}
        defaultValues={() => ({ code: "", name: "", device: 0 })}
        fetchDetail={(id) =>
          payApi.payTypes().then((list) => {
            const item = list.find((x) => x.id === id)
            return (item ?? {}) as unknown as Record<string, unknown>
          })
        }
        onSubmit={async (id, values) => {
          const payload = { code: values.code, name: values.name, device: Number(values.device) }
          if (id) await payApi.updatePayType(id, payload)
          else await payApi.createPayType(payload)
          refresh()
          fetchStats()
          toast.success(id ? "支付方式已更新" : "支付方式已创建")
        }}
        createTitle={"新增支付方式"}
        editTitle={"修改支付方式"}
      />

      <ConfirmDialog
        open={!!deleteId}
        onOpenChange={(open) => { if (!open) setDeleteId(null) }}
        title={"删除"}
        description={"你确实要删除此支付方式吗？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"删除"}
        onConfirm={submitDelete}
      />
    </div>
  )
}
