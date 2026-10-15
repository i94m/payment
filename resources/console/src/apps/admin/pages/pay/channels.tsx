"use client"

import * as React from "react"
import { useNavigate } from "react-router-dom"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { payApi } from "@/api"
import type { ChannelData, ChannelStats, ChannelTestDebug, PluginData, PaymentTypeData, PluginInput } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"
import { numStr } from "@/lib/form-schema"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  ConfirmDialog,
  actionsColumn,
  secondaryCell,
  switchCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import {
  IconSearch, IconRefresh, IconPlus, IconEdit, IconTrash, IconFlask, IconCopy, IconList, IconUpload,
} from "@tabler/icons-react"
import { toast } from "sonner"

// 通道表单值：字段均为字符串（提交前再转数值）；mode/weight/sort 已从表单移除，仅随编辑原值带回
interface ChannelFormValues {
  name: string
  plugin_name: string
  payment_type_id: string
  merchant_rate: string
  cost_rate: string
  min_amount: string
  max_amount: string
  day_limit: string
  day_limit_count: string
  status: string
  mode: string
  weight: string
  sort: string
  debug_log: string
  config: Record<string, string>
}

// 凭证校验依赖所选插件的 inputs schema（动态）：必填凭证新增/编辑均须填写
const channelSchema = (inputs: PluginInput[]) =>
  z.object({
    name: z.string().trim().min(1, "通道名称不能为空"),
    plugin_name: z.string().min(1, "支付插件不能为空"),
    payment_type_id: z.string().min(1, "支付方式不能为空"),
    merchant_rate: numStr("商户费率", { min: 0, max: 100 }),
    cost_rate: numStr("成本费率", { min: 0, max: 100 }),
    min_amount: numStr("单笔最小金额", { min: 0 }),
    max_amount: numStr("单笔最大金额", { min: 0, optional: true }),
    day_limit: numStr("日限额", { min: 0, optional: true }),
    day_limit_count: numStr("单日限笔", { min: 0, integer: true, optional: true }),
    status: z.string(),
    mode: z.string(),
    weight: z.string(),
    sort: z.string(),
    debug_log: z.string(),
    config: z.record(z.string(), z.string()).superRefine((cfg, ctx) => {
      for (const input of inputs) {
        if (input.required && !(cfg[input.name] ?? "").trim()) {
          ctx.addIssue({ code: "custom", path: [input.name], message: `${input.label || input.name}不能为空` })
        }
      }
    }),
  })

// 测试支付：金额范围与后端通道测试口径一致（0.01 ~ 1000）
const testPaySchema = z.object({
  name: z.string(),
  amount: numStr("订单金额", { min: 0.01, max: 1000 }),
})

export function ChannelsPage() {

  const navigate = useNavigate()
  const [typeFilter, setTypeFilter] = React.useState("")
  const [pluginFilter, setPluginFilter] = React.useState("")
  const [payTypes, setPayTypes] = React.useState<PaymentTypeData[]>([])
  const [plugins, setPlugins] = React.useState<PluginData[]>([])
  const [stats, setStats] = React.useState<Record<string, ChannelStats>>({})

  // 编辑弹窗
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [saving, setSaving] = React.useState(false)
  const [deleteId, setDeleteId] = React.useState<number | null>(null)
  // 当前插件凭证渲染 schema（随所选插件变化）
  const [currentInputs, setCurrentInputs] = React.useState<PluginInput[]>([])

  // schema 依赖 editingId/currentInputs，useForm 每次渲染都会读取最新 resolver
  const channelForm = useForm<ChannelFormValues>({
    resolver: zodResolver(channelSchema(currentInputs)),
    defaultValues: {
      name: "", plugin_name: "", payment_type_id: "",
      merchant_rate: "0.00", cost_rate: "0.00", status: "0", mode: "0",
      weight: "1", min_amount: "0.01", max_amount: "0.00", day_limit: "0.00", day_limit_count: "0", sort: "0",
      debug_log: "0",
      config: {},
    },
  })

  // 测试支付弹窗
  const [testChannel, setTestChannel] = React.useState<ChannelData | null>(null)
  const [testing, setTesting] = React.useState(false)
  const testForm = useForm<z.infer<typeof testPaySchema>>({
    resolver: zodResolver(testPaySchema),
    defaultValues: { name: "支付测试", amount: "1.00" },
  })
  // 测试失败调试弹窗
  const [testDebug, setTestDebug] = React.useState<ChannelTestDebug | null>(null)
  // 插件同步（插件目录扫描，同步后刷新插件下拉与通道列表）
  const [syncing, setSyncing] = React.useState(false)
  // 上传安装编译后的插件包（zip：build_plugin.sh 产物，仅 .so + info.json）
  const [uploading, setUploading] = React.useState(false)
  const uploadRef = React.useRef<HTMLInputElement>(null)

  const fetchStats = React.useCallback(() => {
    payApi.channelStats().then(setStats).catch(() => {})
  }, [])

  React.useEffect(() => {
    payApi.payTypes().then(setPayTypes).catch(() => {})
    payApi.plugins().then(setPlugins).catch(() => {})
    fetchStats()
  }, [fetchStats])

  const typeName = (id: number) => payTypes.find((p) => p.id === id)?.name ?? String(id)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (typeFilter !== "") params.payment_type_id = Number(typeFilter)
    if (pluginFilter !== "") params.plugin_name = pluginFilter
    return params
  }, [typeFilter, pluginFilter])

  const handleReset = () => {
    setTypeFilter("")
    setPluginFilter("")
    applyFilters()
  }

  // 同步插件：扫描 plugins/ 目录刷新注册表（合并自原插件管理页，仅保留同步入口）
  const handleSync = async () => {
    setSyncing(true)
    try {
      await payApi.syncPlugins()
      toast.success("同步成功")
      payApi.plugins().then(setPlugins).catch(() => {})
      refresh()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setSyncing(false)
    }
  }

  // 上传插件包：安装到 storage/plugins/（持久卷）并刷新注册表与插件列表
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ""   // 允许重复选择同一文件
    if (!f) return
    setUploading(true)
    try {
      await payApi.uploadPlugin(f)
      toast.success("上传成功")
      payApi.plugins().then(setPlugins).catch(() => {})
      refresh()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "上传失败")
    } finally {
      setUploading(false)
    }
  }

  const openCreate = () => {
    setEditingId(null)
    setCurrentInputs([])
    channelForm.reset({
      name: "", plugin_name: "",
      // 支付方式默认选中下拉框第一项；新增通道默认关闭（后端固定 status=0）
      payment_type_id: payTypes.length > 0 ? String(payTypes[0].id) : "",
      merchant_rate: "0.00", cost_rate: "0.00", status: "0", mode: "0",
      weight: "1", min_amount: "0.01", max_amount: "0.00", day_limit: "0.00", day_limit_count: "0", sort: "0",
      debug_log: "0",
      config: {},
    })
    setDialogOpen(true)
  }

  const openEdit = async (id: number) => {
    try {
      const detail = await payApi.channelDetail(id)
      setEditingId(id)
      setCurrentInputs(detail.inputs || [])
      channelForm.reset({
        name: detail.name,
        plugin_name: detail.plugin_name,
        payment_type_id: String(detail.payment_type_id),
        merchant_rate: detail.merchant_rate,
        cost_rate: detail.cost_rate,
        status: String(detail.status),
        mode: String(detail.mode),
        weight: String(detail.weight),
        min_amount: detail.min_amount,
        max_amount: detail.max_amount,
        day_limit: detail.day_limit,
        day_limit_count: String(detail.day_limit_count),
        sort: String(detail.sort),
        debug_log: String(detail.debug_log ?? 0),
        config: detail.config || {},
      })
      setDialogOpen(true)
    } catch (err) {
      toast.error((err as { message?: string })?.message || "加载失败")
    }
  }

  // 复制通道：以所选通道内容预填新增弹窗
  const openCopy = async (id: number) => {
    try {
      const detail = await payApi.channelDetail(id)
      setEditingId(null)
      setCurrentInputs(detail.inputs || [])
      channelForm.reset({
        name: detail.name,
        plugin_name: detail.plugin_name,
        payment_type_id: String(detail.payment_type_id),
        merchant_rate: detail.merchant_rate,
        cost_rate: detail.cost_rate,
        status: "0",
        mode: String(detail.mode),
        weight: String(detail.weight),
        min_amount: detail.min_amount,
        max_amount: detail.max_amount,
        day_limit: detail.day_limit,
        day_limit_count: String(detail.day_limit_count),
        sort: String(detail.sort),
        debug_log: "0",
        config: detail.config || {},
      })
      setDialogOpen(true)
    } catch (err) {
      toast.error((err as { message?: string })?.message || "加载失败")
    }
  }

  // 测试支付：在测试商户名下按本通道建单，打开收银台走完整支付流程
  const submitTest = testForm.handleSubmit(async (values) => {
    if (!testChannel) return
    setTesting(true)
    try {
      const data = await payApi.testChannelPay(testChannel.id, values)
      setTestChannel(null)
      toast.success("测试支付已发起", { description: data.trade_no })
      window.open(data.cashier_url, "_blank")
      fetchStats()
    } catch (err) {
      const e = err as { message?: string; errors?: unknown[] }
      const debug = Array.isArray(e?.errors) && e.errors.length > 0 ? e.errors[0] : null
      setTestDebug(debug as ChannelTestDebug | null)
      if (!debug) toast.error(e?.message || "操作失败")
    } finally {
      setTesting(false)
    }
  })

  const formPluginName = channelForm.watch("plugin_name")
  const formTypeId = channelForm.watch("payment_type_id")
  const selectedPlugin = plugins.find((p) => p.name === formPluginName)

  // 插件下拉按所选支付方式动态过滤：仅展示 supported_types 覆盖该支付方式调用值的插件。
  // 编辑时当前插件始终保留（历史数据可能未标注该支付方式）。
  const selectedTypeCode = payTypes.find((t) => String(t.id) === formTypeId)?.code
  const availablePlugins = React.useMemo(() => {
    const list = selectedTypeCode
      ? plugins.filter((p) => p.supported_types.includes(selectedTypeCode))
      : plugins
    if (editingId && formPluginName && !list.some((p) => p.name === formPluginName)) {
      const current = plugins.find((p) => p.name === formPluginName)
      if (current) list.push(current)
    }
    return list
  }, [plugins, selectedTypeCode, editingId, formPluginName])

  // 切换支付方式后，当前插件若不支持则清空选择（连带凭证）
  const onTypeChange = (v: string) => {
    const code = payTypes.find((t) => String(t.id) === v)?.code
    if (code && formPluginName) {
      const supported = plugins
        .find((p) => p.name === formPluginName)
        ?.supported_types.includes(code)
      if (!supported) {
        channelForm.setValue("plugin_name", "")
        setCurrentInputs([])
        channelForm.setValue("config", {})
        channelForm.clearErrors("config")
      }
    }
  }

  // 换插件后凭证已清空，旧凭证错误一并清除
  const onPluginChange = (name: string) => {
    const plugin = plugins.find((p) => p.name === name)
    setCurrentInputs(plugin?.inputs || [])
    channelForm.setValue("config", {})
    channelForm.clearErrors("config")
  }

  const submit = channelForm.handleSubmit(async (values) => {
    setSaving(true)
    try {
      // 空值凭证不入库（清空字段即删除该项凭证）
      const cleanedConfig = Object.fromEntries(
        Object.entries(values.config).filter(([, v]) => v !== "" && v != null),
      )
      const payload = {
        name: values.name,
        plugin_name: values.plugin_name,
        payment_type_id: Number(values.payment_type_id),
        merchant_rate: values.merchant_rate || "0.00",
        cost_rate: values.cost_rate || "0.00",
        status: Number(values.status || 1),
        // 模式/权重/排序已从表单移除：原值带回，避免编辑时被重置（后端通道路由仍使用）
        mode: Number(values.mode || 0),
        weight: Number(values.weight || 1),
        min_amount: values.min_amount || "0.01",
        max_amount: values.max_amount || "0.00",
        day_limit: values.day_limit || "0.00",
        day_limit_count: Number(values.day_limit_count || 0),
        sort: Number(values.sort || 0),
        debug_log: values.debug_log === "1" ? 1 : 0,
        config: cleanedConfig,
      }
      if (editingId) await payApi.updateChannel(editingId, payload)
      else await payApi.createChannel(payload)
      toast.success(editingId ? "通道已更新" : "通道已创建")
      setDialogOpen(false)
      refresh()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setSaving(false)
    }
  })

  const toggleStatus = async (item: ChannelData) => {
    try {
      await payApi.updateChannel(item.id, {
        name: item.name,
        plugin_name: item.plugin_name,
        payment_type_id: item.payment_type_id,
        merchant_rate: item.merchant_rate,
        cost_rate: item.cost_rate,
        status: item.status === 1 ? 0 : 1,
        mode: item.mode,
        weight: item.weight,
        min_amount: item.min_amount,
        max_amount: item.max_amount,
        day_limit: item.day_limit,
        day_limit_count: item.day_limit_count,
        sort: item.sort,
        debug_log: item.debug_log ?? 0,
        config: {},
      })
      updateRow(item.id, { status: item.status === 1 ? 0 : 1 })
      toast.success("状态已更新")
      fetchStats()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  const submitDelete = async () => {
    if (!deleteId) return
    try {
      await payApi.deleteChannel(deleteId)
      setDeleteId(null)
      refresh()
      toast.success("删除成功")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  const {
    table, loading, applyFilters, refresh, updateRow,
  } = useDataList<ChannelData>({
    fetchFn: (params) => payApi.channels(params as never),
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
        accessorKey: "name",
        enableSorting: false,
        header: () => "通道名称",
        cell: ({ row }) => (
          <span className="block max-w-full truncate text-sm font-medium" title={row.original.name}>
            {row.original.name}
          </span>
        ),
        meta: { headClassName: "w-[110px]", cellClassName: "max-w-[110px]" },
      },
      {
        accessorKey: "mode",
        enableSorting: false,
        header: () => "模式",
        cell: ({ getValue }) => secondaryCell(getValue() === 1 ? "轮询" : "普通"),
        meta: { headClassName: "w-[70px]" },
      },
      {
        accessorKey: "plugin_name",
        enableSorting: false,
        header: () => "支付插件",
        cell: ({ row }) => (
          <span className="block max-w-full truncate text-sm text-muted-foreground" title={row.original.plugin_name}>
            {row.original.plugin_name}
          </span>
        ),
        meta: { headClassName: "w-[90px]", cellClassName: "max-w-[90px]" },
      },
      {
        accessorKey: "payment_type_id",
        enableSorting: false,
        header: () => "支付方式",
        cell: ({ row }) => <span className="text-sm">{typeName(row.original.payment_type_id)}</span>,
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "merchant_rate",
        enableSorting: false,
        header: () => "商户费率(%)",
        cell: ({ row }) => secondaryCell(row.original.merchant_rate),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "cost_rate",
        enableSorting: false,
        header: () => "成本费率(%)",
        cell: ({ row }) => secondaryCell(row.original.cost_rate),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "today_amount",
        enableSorting: false,
        header: () => "今日收款",
        cell: ({ row }) => secondaryCell(stats[String(row.original.id)]?.today_amount ?? "-"),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "yesterday_amount",
        enableSorting: false,
        header: () => "昨日收款",
        cell: ({ row }) => secondaryCell(stats[String(row.original.id)]?.yesterday_amount ?? "-"),
        meta: { headClassName: "w-[90px]" },
      },
      {
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => switchCell(row.original.status === 1, () => toggleStatus(row.original)),
        meta: { headClassName: "w-[70px]" },
      },
      actionsColumn([
        { label: "测试", icon: <IconFlask className="size-4" />, onClick: (item) => { testForm.reset({ name: "支付测试", amount: "1.00" }); setTestChannel(item) } },
        { label: "编辑", icon: <IconEdit className="size-4" />, onClick: (item) => openEdit(item.id) },
        { label: "复制", icon: <IconCopy className="size-4" />, onClick: (item) => openCopy(item.id) },
        {
          label: "删除",
          icon: <IconTrash className="size-4" />,
          variant: "destructive",
          onClick: (item) => setDeleteId(item.id),
        },
        {
          label: "订单",
          icon: <IconList className="size-4" />,
          onClick: (item) => navigate(`/trade/orders?channel_id=${item.id}`),
        },
      ] as ActionItem<ChannelData>[], { header: "操作", width: 360 }),
    ],
    perPage: 20,
    onError: () => toast.error("操作失败"),
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 items-end max-w-2xl">
            <Select value={typeFilter || "_all"} onValueChange={(v) => setTypeFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"支付方式"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部支付方式"}</SelectItem>
                {payTypes.map((pt) => (
                  <SelectItem key={pt.id} value={String(pt.id)}>{pt.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={pluginFilter || "_all"} onValueChange={(v) => setPluginFilter(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full">
                <SelectValue placeholder={"支付插件"} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{"全部支付插件"}</SelectItem>
                {plugins.map((p) => (
                  <SelectItem key={p.name} value={p.name}>{p.showname}</SelectItem>
                ))}
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
            <div className="flex items-center gap-2">
              <Button size="sm" variant="outline" onClick={handleSync} disabled={syncing} title="点击同步插件目录">
                <IconRefresh className={`size-4 ${syncing ? "animate-spin" : ""}`} />
                {`已安装插件（${plugins.length}）`}
              </Button>
              <Button size="sm" variant="outline" onClick={() => uploadRef.current?.click()}
                      disabled={uploading} title="上传编译后的插件包（zip：build_plugin.sh 产物，仅 .so + info.json）">
                <IconUpload className={`size-4 ${uploading ? "animate-pulse" : ""}`} />
                {uploading ? "上传中…" : "上传插件"}
              </Button>
              <input ref={uploadRef} type="file" accept=".zip" className="hidden" onChange={handleUpload} />
              <Button size="sm" onClick={openCreate}>
                <IconPlus className="size-4" />
                {"新增通道"}
              </Button>
            </div>
          </div>
        </FilterCard>

        <div className="px-4 lg:px-6">
          <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
            <DataTablePagination table={table} />
          </DataTable>
        </div>
      </div>

      {/* 新建/编辑通道 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingId ? "编辑通道" : "新增通道"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={submit}>
            <div className="grid grid-cols-2 gap-3">
              <Controller
                name="name"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid} className="col-span-2">
                    <FieldLabel htmlFor={field.name}>{"通道名称"}<RequiredMark /></FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"请输入通道名称"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="payment_type_id"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"支付方式"}<RequiredMark /></FieldLabel>
                    <Select value={field.value || undefined} onValueChange={(v) => { field.onChange(v); onTypeChange(v) }}>
                      <SelectTrigger id={field.name} className="w-full" aria-invalid={fieldState.invalid || undefined}><SelectValue placeholder={"选择支付方式"} /></SelectTrigger>
                      <SelectContent>
                        {payTypes.map((pt) => (
                          <SelectItem key={pt.id} value={String(pt.id)}>{pt.name}（{pt.code}）</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="plugin_name"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"支付插件"}<RequiredMark /></FieldLabel>
                    <Select value={field.value || undefined} onValueChange={(v) => { field.onChange(v); onPluginChange(v) }} disabled={!!editingId}>
                      <SelectTrigger id={field.name} className="w-full" aria-invalid={fieldState.invalid || undefined}><SelectValue placeholder={"选择支付插件"} /></SelectTrigger>
                      <SelectContent>
                        {availablePlugins.length === 0 && (
                          <div className="px-2 py-1.5 text-xs text-muted-foreground">{"该支付方式暂无可用插件"}</div>
                        )}
                        {availablePlugins.map((p) => (
                          <SelectItem key={p.name} value={p.name}>{p.showname}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="merchant_rate"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"商户费率(%)"}<RequiredMark /></FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"如 1.5 表示 1.5%"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="cost_rate"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"成本费率(%)"}<RequiredMark /></FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"如 1.5 表示 1.5%"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="min_amount"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"单笔最小"}<RequiredMark /></FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"如 1"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="max_amount"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"单笔最大"}</FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"如 5000，0 或留空不限"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="day_limit"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"日限额"}</FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"如 10000，0 或留空不限"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="day_limit_count"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"单日限笔"}</FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      placeholder={"如 1000，0 或留空不限"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="debug_log"
                control={channelForm.control}
                render={({ field }) => (
                  <Field className="col-span-2">
                    <FieldLabel htmlFor={field.name}>{"调试日志"}</FieldLabel>
                    <Select value={field.value || "0"} onValueChange={field.onChange}>
                      <SelectTrigger id={field.name} className="w-full"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="0">{"关闭"}</SelectItem>
                        <SelectItem value="1">{"开启"}</SelectItem>
                      </SelectContent>
                    </Select>
                    <p className="text-xs text-muted-foreground">
                      {"开启后下单/回调/查单交互写入 /tmp/pay_日期_插件.log（当天排障用，重启丢失）"}
                    </p>
                  </Field>
                )}
              />

              {/* 插件凭证（按 inputs schema 动态渲染） */}
              {currentInputs.length > 0 && (
                <div className="col-span-2 space-y-2 rounded-lg border p-3">
                  <p className="text-sm font-medium">{"通道凭证"}（{selectedPlugin?.showname}）</p>
                  {currentInputs.map((input) => (
                    <Controller
                      key={input.name}
                      name={`config.${input.name}`}
                      control={channelForm.control}
                      render={({ field, fieldState }) => (
                        <Field data-invalid={fieldState.invalid}>
                          <FieldLabel htmlFor={field.name} className="text-xs">
                            {input.label || input.name}{input.required && <RequiredMark />}
                          </FieldLabel>
                          <Input
                            id={field.name}
                            className="h-8 font-mono text-xs"
                            value={field.value ?? ""}
                            onChange={(e) => field.onChange(e.target.value)}
                            aria-invalid={fieldState.invalid || undefined}
                            placeholder={input.placeholder}
                          />
                          {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                        </Field>
                      )}
                    />
                  ))}
                </div>
              )}
            </div>
            <DialogFooter className="mt-6">
              <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>{"取消"}</Button>
              <Button type="submit" disabled={saving}>{"保存设置"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 测试支付：建测试单打开收银台走完整流程 */}
      <Dialog open={!!testChannel} onOpenChange={(open) => { if (!open) setTestChannel(null) }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"测试支付"}{testChannel ? ` - ${testChannel.name}` : ""}</DialogTitle>
          </DialogHeader>
          <form onSubmit={submitTest}>
            <div className="grid gap-3">
              <Controller
                name="name"
                control={testForm.control}
                render={({ field }) => (
                  <Field>
                    <FieldLabel htmlFor={field.name}>{"订单名称"}</FieldLabel>
                    <Input id={field.name} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} />
                  </Field>
                )}
              />
              <Controller
                name="amount"
                control={testForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"订单金额"}<RequiredMark /></FieldLabel>
                    <Input
                      id={field.name}
                      type="number" min="0.01" step="0.01"
                      value={field.value ?? ""}
                      onChange={(e) => field.onChange(e.target.value)}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
            </div>
            <DialogFooter className="mt-6">
              <Button type="button" variant="outline" onClick={() => setTestChannel(null)}>{"取消"}</Button>
              <Button type="submit" disabled={testing}>{"发起测试"}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 测试失败调试弹窗：请求参数/IP/上游响应，方便排障 */}
      <Dialog open={!!testDebug} onOpenChange={(open) => { if (!open) setTestDebug(null) }}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="text-destructive">{"测试失败"} - {"调试信息"}</DialogTitle>
          </DialogHeader>
          {testDebug && (
            <div className="space-y-3 text-sm">
              <div className="rounded-lg border p-3 space-y-1.5">
                <p className="font-medium">{"基本信息"}</p>
                <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                  <span className="text-muted-foreground">{"时间"}</span><span className="font-mono">{testDebug.time}</span>
                  <span className="text-muted-foreground">{"发起 IP"}</span><span className="font-mono">{testDebug.client_ip || "-"}</span>
                  <span className="text-muted-foreground">{"错误信息"}</span><span className="font-mono text-destructive break-all">{testDebug.error}</span>
                </div>
              </div>
              <div className="rounded-lg border p-3 space-y-1.5">
                <p className="font-medium">{"通道信息"}</p>
                <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-32 overflow-auto bg-muted/50 rounded p-2">
                  {JSON.stringify(testDebug.channel, null, 2)}
                </pre>
                <p className="font-medium">{"订单信息"}</p>
                <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-32 overflow-auto bg-muted/50 rounded p-2">
                  {JSON.stringify(testDebug.order, null, 2)}
                </pre>
              </div>
              <div className="rounded-lg border p-3 space-y-1.5">
                <p className="font-medium">{"上游请求"}</p>
                {testDebug.upstream_request ? (
                  <>
                    <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                      <span className="text-muted-foreground">URL</span><span className="font-mono break-all">{String(testDebug.upstream_request.url)}</span>
                      <span className="text-muted-foreground">HTTP</span><span className="font-mono">{String(testDebug.upstream_request.http_status ?? "-")} / {String(testDebug.upstream_request.duration_ms ?? "-")}ms</span>
                    </div>
                    <p className="text-xs text-muted-foreground">{"请求参数"}</p>
                    <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto bg-muted/50 rounded p-2">
                      {JSON.stringify(testDebug.upstream_request.params, null, 2)}
                    </pre>
                    <p className="text-xs text-muted-foreground">{"响应内容"}</p>
                    <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto bg-muted/50 rounded p-2">
                      {String(testDebug.upstream_request.body ?? "-")}
                    </pre>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">{"无上游请求记录（未走到插件下单环节）"}</p>
                )}
              </div>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => { setTestDebug(null); submitTest() }} disabled={testing}>{"重试"}</Button>
            <Button onClick={() => setTestDebug(null)}>{"关闭"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteId}
        onOpenChange={(open) => { if (!open) setDeleteId(null) }}
        title={"删除"}
        description={"确定删除该通道？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"删除"}
        onConfirm={submitDelete}
      />
    </div>
  )
}
