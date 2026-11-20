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
  ListError,
  actionsColumn,
  secondaryCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import { IconPlus, IconEdit, IconTrash, IconList } from "@tabler/icons-react"
import { PayIcon } from "@/components/pay-icon"
import { DEFAULT_PAY_ICON, PAY_ICON_LIBRARY } from "@/lib/pay-icons"
import { z } from "zod"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"

// 支持设备（库里 0=PC+Mobile 1=PC 2=Mobile）：表单拆成两个开关，可同时勾选
const deviceLabel = (v: number) => (v === 1 ? "PC" : v === 2 ? "Mobile" : "PC+Mobile")

/** 表单两个开关 → 存储值 */
const deviceValue = (pc: boolean, mobile: boolean) => (pc && mobile ? 0 : pc ? 1 : 2)

const typeSchema = z.object({
  // 调用值仅限英文数字，与支付文档一致（^[a-zA-Z0-9]+$）
  code: z.string().min(1).regex(/^[a-zA-Z0-9]+$/, "调用值仅限英文与数字"),
  name: z.string().min(1),
  // 是否支持 PC / Mobile：至少选一个（保存时折算回 device）
  device_pc: z.boolean(),
  device_mobile: z.boolean(),
  // 图标：从内置图标库选择，留空表示列表/收银台只显示文字
  icon: z.string().optional().default(""),
}).refine((v) => v.device_pc || v.device_mobile, {
  message: "请至少选择一种支持设备",
  path: ["device_pc"],
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
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitDelete = async () => {
    if (!deleteId) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    await payApi.deletePayType(deleteId)
    setDeleteId(null)
    refresh()
    toast.success("删除成功")
  }

  const {
    table, total, loading, error, refresh, updateRow,
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
        // 「名称」列：图标来自接口 icon 字段（未配置时按调用值取内置图标库）
        header: () => "名称",
        cell: ({ row }) => (
          <span className="flex items-center gap-1.5 text-sm font-medium">
            <PayIcon code={row.original.code} icon={row.original.icon} />
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
        meta: { headClassName: "w-[100px] text-right", cellClassName: "text-right" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => (
          <div className="flex items-center justify-center gap-2">
            <Switch checked={row.original.status === 1} onCheckedChange={() => toggleStatus(row.original)} />
            <span className="text-xs text-muted-foreground">
              {row.original.status === 1 ? "已开启" : "已关闭"}
            </span>
          </div>
        ),
        meta: { headClassName: "min-w-[100px] text-center", cellClassName: "text-center" },
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
          {error ? (
            <ListError error={error} onRetry={refresh} />
          ) : (
            <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
              <DataTablePagination table={table} total={total} />
            </DataTable>
          )}
        </div>
      </div>

      <FormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editingId={editingId}
        schema={typeSchema}
        // 弹框更窄：两列表单，图标库在下方整行铺开
        width="sm:max-w-md max-h-[90vh] overflow-y-auto"
        // 表单字段：调用值/显示名称/支持设备（PC、Mobile 两个复选框同组）/图标
        fields={[
          { type: "input", name: "code", label: "调用值", placeholder: "仅限英文，要与支付文档一致", span: "half", required: true },
          { type: "input", name: "name", label: "显示名称", placeholder: "仅显示使用", span: "half", required: true },
          {
            // 支持设备：PC / Mobile 两个复选框放在同一行，可同时勾选
            type: "checkbox-group", name: "device", label: "支持设备", span: "full",
            items: [
              { name: "device_pc", label: "PC" },
              { name: "device_mobile", label: "Mobile" },
            ],
            // 「至少选一个」的校验错误显示在整组下方
            errorField: "device_pc",
          },
          {
            type: "icon-picker", name: "icon", label: "图标", span: "full",
            // 内置图标库（与 db init 的支付方式种子一一对应），第一项（支付宝）为新增默认值
            options: PAY_ICON_LIBRARY.map((it) => ({ label: it.label, value: it.src })),
          },
        ]}
        defaultValues={() => ({
          code: "", name: "",
          device_pc: true, device_mobile: true,
          icon: DEFAULT_PAY_ICON,
        })}
        fetchDetail={(id) =>
          payApi.payTypes().then((list) => {
            const item = list.find((x) => x.id === id)
            if (!item) return {}
            // device 0=PC+Mobile 1=PC 2=Mobile → 两个开关
            return {
              ...item,
              device_pc: item.device !== 2,
              device_mobile: item.device !== 1,
            } as unknown as Record<string, unknown>
          })
        }
        onSubmit={async (id, values) => {
          const payload = {
            code: String(values.code), name: String(values.name),
            device: deviceValue(!!values.device_pc, !!values.device_mobile),
            icon: String(values.icon ?? ""),
          }
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
