"use client"

import * as React from "react"
import { useNavigate } from "react-router-dom"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { payApi } from "@/api"
import type { ChannelData, ChannelStats, ChannelTestDebug, ChannelTestPayResult, PluginData, PaymentTypeData, PluginInput } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { numStr } from "@/lib/form-schema"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import { PayIcon, PayTypeLabel } from "@/components/pay-icon"
import {
  useDataList,
  ConfirmDialog,
  ListError,
  actionsColumn,
  secondaryCell,
  switchCell,
} from "@/components/data-list/"
import type { ActionItem } from "@/components/data-list/"
import {
  IconSearch, IconRefresh, IconPlus, IconEdit, IconTrash, IconFlask, IconCopy, IconList, IconUpload,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { useAuthStore } from "@/stores/auth-store"
import { toIntParam } from "@/lib/form-utils"

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

// 凭证校验依赖所选插件的 inputs schema（动态）：必填凭证新增/编辑均须填写。
// 所有字段都带 default("")/default({})：字段未挂载（如已从表单移除的费率/限额）
// 时 RHF 可能给出 undefined，没有默认值会抛 zod 的英文基础错误。
const channelSchema = (inputs: PluginInput[]) =>
  z.object({
    name: z.string().trim().min(1, "通道名称不能为空").default(""),
    plugin_name: z.string().min(1, "支付插件不能为空").default(""),
    payment_type_id: z.string().min(1, "支付方式不能为空").default(""),
    merchant_rate: numStr("商户费率", { min: 0, max: 100 }),
    cost_rate: numStr("成本费率", { min: 0, max: 100 }),
    min_amount: numStr("单笔最小金额", { min: 0 }),
    max_amount: numStr("单笔最大金额", { min: 0, optional: true }),
    day_limit: numStr("日限额", { min: 0, optional: true }),
    day_limit_count: numStr("单日限笔", { min: 0, integer: true, optional: true }),
    status: z.string().default("0"),
    mode: z.string().default("0"),
    weight: z.string().default("1"),
    sort: z.string().default("0"),
    debug_log: z.string().default("0"),
    config: z.record(z.string(), z.string().default("")).default({}).superRefine((cfg, ctx) => {
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

// 插件展示名：内置与三方一视同仁，不额外标注来源
const pluginLabel = (p: PluginData) => p.showname

/** 插件 inputs 的 options 兼容对象（{value: label}）与数组两种写法 */
function inputOptions(input: PluginInput): { value: string; label: string }[] {
  const raw = input.options
  if (!raw) return []
  if (Array.isArray(raw)) return raw.map((v) => ({ value: String(v), label: String(v) }))
  return Object.entries(raw as Record<string, unknown>)
    .map(([value, label]) => ({ value, label: String(label) }))
}

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

  // 通道凭证：编辑/复制时直接取明文（后端要求 channel.update，无权限时回落脱敏），
  // 不再提供「显示/隐藏」按钮 —— 打开弹窗即看到全部凭证。
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const canRevealConfig = !!user?.is_super_admin || permissions.includes("*") || permissions.includes("channel.update")
  // 上传插件与「刷新插件」同口径：持有「支付通道-更新」即可（包本身另有签名校验）
  const canUploadPlugin = canRevealConfig

  // schema 依赖 editingId/currentInputs，useForm 每次渲染都会读取最新 resolver。
  // 表单输入用 z.input（允许字段缺省，schema 里有 default），提交拿到 z.output（全是字符串）
  const channelForm = useForm<z.input<ReturnType<typeof channelSchema>>, unknown, ChannelFormValues>({
    resolver: zodResolver(channelSchema(currentInputs)),
    defaultValues: {
      name: "", plugin_name: "", payment_type_id: "",
      merchant_rate: "0.00", cost_rate: "0.00", status: "0", mode: "0",
      weight: "1", min_amount: "0.01", max_amount: "0.00", day_limit: "0.00", day_limit_count: "0", sort: "0",
      debug_log: "0",
      config: {},
    },
  })

  // 通道名称是否被用户手动改过：未改过时随「支付方式 + 插件」自动生成
  const nameTouchedRef = React.useRef(false)

  // 测试支付弹窗
  const [testChannel, setTestChannel] = React.useState<ChannelData | null>(null)
  const [testing, setTesting] = React.useState(false)
  // 本次测试落库的测试单（成功后留在弹窗里，可直接跳订单页核对回调）
  const [testResult, setTestResult] = React.useState<ChannelTestPayResult | null>(null)
  const testForm = useForm<z.input<typeof testPaySchema>, unknown, z.output<typeof testPaySchema>>({
    resolver: zodResolver(testPaySchema),
    defaultValues: { name: "支付测试", amount: "1.00" },
  })
  // 测试失败调试弹窗
  const [testDebug, setTestDebug] = React.useState<ChannelTestDebug | null>(null)
  // 插件刷新（插件目录扫描，刷新后同步插件下拉与通道列表）
  const [syncing, setSyncing] = React.useState(false)
  // 上传安装编译后的插件包（zip：build_plugin.sh 产物，仅 .so + signature.json）；
  // 后端要「支付通道-更新」权限（装插件 = 进程内执行 .so，包另需 Ed25519 签名），前端同口径显隐
  const [uploading, setUploading] = React.useState(false)
  const uploadRef = React.useRef<HTMLInputElement>(null)

  const fetchStats = React.useCallback(() => {
    payApi.channelStats().then(setStats).catch(() => {})
  }, [])

  // 支付方式/插件下拉是建单与凭证渲染的前置数据，加载失败必须显式提示，
  // 否则弹窗里会静默出现空下拉（看起来像「没有可用插件」）
  React.useEffect(() => {
    payApi.payTypes().then(setPayTypes)
      .catch((err) => toast.error(getErrorMessage(err, "支付方式加载失败")))
    payApi.plugins().then(setPlugins)
      .catch((err) => toast.error(getErrorMessage(err, "支付插件加载失败")))
    fetchStats()
  }, [fetchStats])

  const typeItem = (id: number) => payTypes.find((p) => p.id === id)
  const typeName = (id: number) => typeItem(id)?.name ?? String(id)

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    const tid = toIntParam(typeFilter)
    if (tid !== undefined) params.payment_type_id = tid
    if (pluginFilter !== "") params.plugin_name = pluginFilter
    return params
  }, [typeFilter, pluginFilter])

  const handleReset = () => {
    setTypeFilter("")
    setPluginFilter("")
    applyFilters()
  }

  // 刷新插件：扫描 plugins/ 目录刷新注册表（合并自原插件管理页，仅保留刷新入口）
  const handleSync = async () => {
    setSyncing(true)
    try {
      const res = await payApi.syncPlugins()
      toast.success(res?.message || "同步成功")
      // 有插件加载失败时只提示数量与插件名（内部异常细节留在服务端日志）
      const failures = Object.keys(res?.errors || {})
      if (failures.length > 0) {
        toast.warning(`${failures.length} 个插件加载失败：${failures.join("、")}`, {
          description: "请确认插件包与当前系统环境匹配，详情见服务端日志",
          duration: 8000,
        })
      }
      payApi.plugins().then(setPlugins).catch(() => {})
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setSyncing(false)
    }
  }

  // 上传插件包：安装到 storage/plugins/（持久卷）并刷新注册表与插件列表。
  // 编译镜像会校验包内 signature.json，未签名的包会被后端拒绝。
  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    e.target.value = ""   // 允许重复选择同一文件
    if (!f) return
    if (!f.name.toLowerCase().endsWith(".zip")) {
      toast.error("请选择 .zip 格式的插件包")
      return
    }
    setUploading(true)
    try {
      const res = await payApi.uploadPlugin(f)
      const label = [res?.showname, res?.version].filter(Boolean).join(" ")
      toast.success(label ? `${label} 安装成功` : (res?.note || "安装成功"))
      payApi.plugins().then(setPlugins).catch(() => {})
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "上传失败"))
    } finally {
      setUploading(false)
    }
  }

  const openCreate = () => {
    setEditingId(null)
    setCurrentInputs([])
    // 新增时名称跟随插件自动生成，直到用户手动改过
    nameTouchedRef.current = false
    lastPluginRef.current = ""
    configCacheRef.current = {}
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
      const detail = await payApi.channelDetail(id, { reveal: true })
      setEditingId(id)
      nameTouchedRef.current = true
      // 编辑允许换插件：先记下当前插件与其凭证，切走再切回来能恢复
      lastPluginRef.current = detail.plugin_name
      configCacheRef.current = { [detail.plugin_name]: { ...(detail.config || {}) } }
      setCurrentInputs(detail.inputs || [])
      if (!detail.config_revealed) {
        toast.error("无权限查看明文凭证", { description: "需要「支付通道-更新」权限，当前显示为脱敏值" })
      }
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
      toast.error(getErrorMessage(err, "加载失败"))
    }
  }

  // 复制通道：以所选通道内容预填新增弹窗
  const openCopy = async (id: number) => {
    try {
      const detail = await payApi.channelDetail(id, { reveal: true })
      setEditingId(null)
      nameTouchedRef.current = true
      lastPluginRef.current = detail.plugin_name
      configCacheRef.current = { [detail.plugin_name]: { ...(detail.config || {}) } }
      setCurrentInputs(detail.inputs || [])
      if (!detail.config_revealed) {
        toast.error("无权限查看明文凭证", { description: "需要「支付通道-更新」权限，当前显示为脱敏值" })
      }
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
      toast.error(getErrorMessage(err, "加载失败"))
    }
  }

  // 通道测试：用该通道凭证真实下一笔单（落测试单 order_type=2）。
  // 测试单可在订单页核对回调结果，但不入余额/统计、不投递商户通知。
  // 拿到支付信息后：链接类直接新窗口打开，二维码/HTML 提示人工查看。
  const submitTest = testForm.handleSubmit(async (values) => {
    if (!testChannel) return
    setTesting(true)
    setTestResult(null)
    try {
      const data = await payApi.testChannelPay(testChannel.id, values)
      setTestResult(data)
      const url = /^https?:\/\//.test(data.pay_info || "") ? data.pay_info : ""
      if (url && data.pay_type !== "html") {
        window.open(url, "_blank")
        toast.success(`测试通过：${data.pay_type}`, { description: `测试单 ${data.trade_no} 已创建，支付后可在订单页核对状态` })
      } else if (data.pay_type === "qrcode" || data.pay_type === "html") {
        toast.success(`测试通过：${data.pay_type}`, { description: "支付内容见弹窗，测试单已创建" })
        console.info("[channel testpay]", data)
      } else {
        toast.success(`测试通过：${data.pay_type}`)
      }
      fetchStats()
    } catch (err) {
      const e = err as { errors?: unknown[] }
      const debug = Array.isArray(e?.errors) && e.errors.length > 0 ? e.errors[0] : null
      setTestDebug(debug as ChannelTestDebug | null)
      // 有上游调试详情时用调试弹窗展示，否则回落到 toast 后端原因
      if (!debug) toast.error(getErrorMessage(err, "测试支付失败"))
    } finally {
      setTesting(false)
    }
  })

  const formPluginName = channelForm.watch("plugin_name")
  const formTypeId = channelForm.watch("payment_type_id")

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
      toast.error(getErrorMessage(err, "操作失败"))
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
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  // 调试日志开关（列表内切换）：走专用接口，只改 debug_log，不重写费率/限额等其他字段
  const toggleDebugLog = async (item: ChannelData) => {
    const next = item.debug_log === 1 ? 0 : 1
    try {
      await payApi.setChannelDebugLog(item.id, next === 1)
      updateRow(item.id, { debug_log: next })
      toast.success(next === 1 ? "调试日志已开启" : "调试日志已关闭")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitDelete = async () => {
    if (!deleteId) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    await payApi.deleteChannel(deleteId)
    setDeleteId(null)
    refresh()
    toast.success("删除成功")
  }

  const {
    table, data: channelRows, total, loading, error, applyFilters, refresh, updateRow,
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
        cell: ({ row }) => {
          const pt = typeItem(row.original.payment_type_id)
          return <PayTypeLabel code={pt?.code} icon={pt?.icon} name={typeName(row.original.payment_type_id)}
                               className="text-sm" />
        },
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
      {
        accessorKey: "debug_log",
        enableSorting: false,
        header: () => "调试日志",
        cell: ({ row }) => switchCell(row.original.debug_log === 1, () => toggleDebugLog(row.original)),
        meta: { headClassName: "w-[90px] text-center", cellClassName: "text-center" },
      },
      actionsColumn([
        { label: "测试", icon: <IconFlask className="size-4" />, onClick: (item) => { testForm.reset({ name: "支付测试", amount: "1.00" }); setTestResult(null); setTestChannel(item) } },
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
  })

  // ---- 通道名称自动生成：新增时填插件显示名，重名自动加 -2/-3 ----
  const makeChannelName = (pluginName?: string) => {
    if (!pluginName) return ""
    const plugin = plugins.find((p) => p.name === pluginName)
    if (!plugin) return ""
    const base = plugin.showname
    const taken = new Set(channelRows.map((c) => c.name))
    if (!taken.has(base)) return base
    for (let i = 2; i < 100; i++) {
      const candidate = `${base}-${i}`
      if (!taken.has(candidate)) return candidate
    }
    return base
  }

  // 只在新增、且用户还没手动改过名称时自动填充
  const applyAutoName = (pluginName?: string) => {
    if (editingId || nameTouchedRef.current) return
    const auto = makeChannelName(pluginName)
    if (!auto) return
    channelForm.setValue("name", auto, { shouldValidate: false })
    channelForm.clearErrors("name")
  }

  // 当前表单里填着的凭证，按「所属插件」缓存：切换插件再切回来能恢复（未保存的编辑）
  const lastPluginRef = React.useRef("")
  const configCacheRef = React.useRef<Record<string, Record<string, string>>>({})

  // 整体替换凭证：先写 config 对象，再逐字段写一遍 —— 不同插件可能有同名字段
  // （如 appurl/appkey），只 setValue("config", …) 时旧插件的输入框不会刷新
  const applyConfig = (config: Record<string, string>) => {
    const current = (channelForm.getValues("config") || {}) as Record<string, string>
    const keys = new Set([...Object.keys(current), ...Object.keys(config)])
    const merged: Record<string, string> = {}
    keys.forEach((k) => { merged[k] = config[k] ?? "" })
    channelForm.setValue("config", merged)
    Object.entries(merged).forEach(([k, v]) => {
      channelForm.setValue(`config.${k}`, v, { shouldValidate: false })
    })
  }

  const rememberConfig = (pluginName: string) => {
    if (!pluginName) return
    const current = channelForm.getValues("config") as Record<string, string> | undefined
    configCacheRef.current[pluginName] = { ...(current || {}) }
  }

  // 换插件：按新插件的 inputs 渲染表单，凭证回填该插件之前填过的值（没有则清空）
  const onPluginChange = (name: string) => {
    const prev = lastPluginRef.current
    if (prev && prev !== name) rememberConfig(prev)
    lastPluginRef.current = name

    const plugin = plugins.find((p) => p.name === name)
    setCurrentInputs(plugin?.inputs || [])
    applyConfig({ ...(configCacheRef.current[name] || {}) })
    channelForm.clearErrors("config")
    applyAutoName(name)
  }

  // 切换支付方式后，当前插件若不支持则清空选择（凭证先按插件缓存，切回来可恢复）
  const onTypeChange = (v: string) => {
    const code = payTypes.find((t) => String(t.id) === v)?.code
    if (code && formPluginName) {
      const supported = plugins
        .find((p) => p.name === formPluginName)
        ?.supported_types.includes(code)
      if (!supported) {
        rememberConfig(formPluginName)
        lastPluginRef.current = ""
        channelForm.setValue("plugin_name", "")
        setCurrentInputs([])
        applyConfig({})
        channelForm.clearErrors("config")
        // 插件被清空：自动生成的名称一并清掉，避免留下旧插件的名称
        if (!editingId && !nameTouchedRef.current) channelForm.setValue("name", "")
        return
      }
    }
    // 名称只跟插件走，切换支付方式不影响已填的名称
  }

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
                  <SelectItem key={pt.id} value={String(pt.id)}>
                    <span className="flex items-center gap-1.5">
                      <PayIcon code={pt.code} icon={pt.icon} />
                      {pt.name}
                    </span>
                  </SelectItem>
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
                  <SelectItem key={p.name} value={p.name}>{pluginLabel(p)}</SelectItem>
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
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button size="sm" variant="outline" onClick={handleSync} disabled={syncing}
                          aria-label={"刷新插件"}>
                    <IconRefresh className={`size-4 ${syncing ? "animate-spin" : ""}`} />
                    {"刷新插件"}
                    <Badge variant="secondary" className="h-4 px-1 text-[10px] tabular-nums">
                      {plugins.length}
                    </Badge>
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top">{"重新扫描插件目录，刷新插件列表"}</TooltipContent>
              </Tooltip>
              {/* 上传插件：需「支付通道-更新」权限（与后端 PermissionChecker 一致），仅接受已签名的 .so 包 */}
              {canUploadPlugin && (
                <>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button size="sm" variant="outline" onClick={() => uploadRef.current?.click()}
                              disabled={uploading} aria-label={"上传插件"}>
                        <IconUpload className={`size-4 ${uploading ? "animate-pulse" : ""}`} />
                        {uploading ? "上传中…" : "上传插件"}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top">{"上传插件包（仅支持 .zip）"}</TooltipContent>
                  </Tooltip>
                  <input ref={uploadRef} type="file" accept=".zip" className="hidden" onChange={handleUpload} />
                </>
              )}
              <Button size="sm" onClick={openCreate}>
                <IconPlus className="size-4" />
                {"新增通道"}
              </Button>
            </div>
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

      {/* 新建/编辑通道 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-xl max-h-[85vh] overflow-y-auto" onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>{editingId ? "编辑通道" : "新增通道"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={submit}>
            <div className="grid grid-cols-2 gap-3">
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
                          <SelectItem key={pt.id} value={String(pt.id)}>
                            <span className="flex items-center gap-1.5">
                              <PayIcon code={pt.code} icon={pt.icon} />
                              {pt.name}（{pt.code}）
                            </span>
                          </SelectItem>
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
                    <Select value={field.value || undefined} onValueChange={(v) => { field.onChange(v); onPluginChange(v) }}>
                      <SelectTrigger id={field.name} className="w-full" aria-invalid={fieldState.invalid || undefined}><SelectValue placeholder={"选择支付插件"} /></SelectTrigger>
                      <SelectContent>
                        {availablePlugins.length === 0 && (
                          <div className="px-2 py-1.5 text-xs text-muted-foreground">{"该支付方式暂无可用插件"}</div>
                        )}
                        {availablePlugins.map((p) => (
                          <SelectItem key={p.name} value={p.name}>{pluginLabel(p)}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="name"
                control={channelForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid} className="col-span-2">
                    <FieldLabel htmlFor={field.name}>{"通道名称"}<RequiredMark /></FieldLabel>
                    <Input
                      id={field.name}
                      value={field.value ?? ""}
                      onChange={(e) => {
                        // 用户一动手就停止自动生成，避免把自定义名称冲掉
                        nameTouchedRef.current = true
                        field.onChange(e.target.value)
                      }}
                      placeholder={"选择支付方式与支付插件后自动填充，也可自行修改"}
                      aria-invalid={fieldState.invalid || undefined}
                    />
                    {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              {/* 费率 / 限额 / 调试日志不再在表单里编辑：值随详情带回原样提交，
                  调试日志在列表里用开关切换；新增时用后端默认值。 */}


              {/* 插件凭证（按 inputs schema 动态渲染）：编辑即明文，不再提供显示/隐藏按钮。
                  标题放在边框模块之上，模块内只放说明与字段。 */}
              {currentInputs.length > 0 && (
                <div className="col-span-2 space-y-2">
                  <p className="text-sm font-medium">{"通道凭证"}</p>
                  <div className="space-y-2 rounded-lg border p-3">
                    {!canRevealConfig && (
                      <p className="text-xs text-amber-600">
                        {"当前账号无「支付通道-更新」权限，凭证以 ****** 脱敏显示"}
                      </p>
                    )}
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
                            {input.type === "textarea" ? (
                              // 公钥/私钥等长凭证：整段粘贴，保留换行便于肉眼核对
                              <Textarea
                                id={field.name}
                                rows={4}
                                className="font-mono text-xs break-all"
                                value={field.value ?? ""}
                                onChange={(e) => field.onChange(e.target.value)}
                                aria-invalid={fieldState.invalid || undefined}
                                placeholder={input.placeholder}
                              />
                            ) : input.type === "select" ? (
                              <Select value={field.value ?? ""} onValueChange={field.onChange}>
                                <SelectTrigger
                                  id={field.name}
                                  className="w-full"
                                  aria-invalid={fieldState.invalid || undefined}
                                >
                                  <SelectValue placeholder={input.placeholder || "请选择"} />
                                </SelectTrigger>
                                <SelectContent>
                                  {inputOptions(input).map((o) => (
                                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            ) : (
                              <Input
                                id={field.name}
                                className="h-8 font-mono text-xs"
                                value={field.value ?? ""}
                                onChange={(e) => field.onChange(e.target.value)}
                                aria-invalid={fieldState.invalid || undefined}
                                placeholder={input.placeholder}
                              />
                            )}
                            {fieldState.error && <FieldError>{fieldState.error.message}</FieldError>}
                          </Field>
                        )}
                      />
                    ))}
                  </div>
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

      {/* 测试支付：建测试单打开支付页，走完整回调链路 */}
      <Dialog open={!!testChannel} onOpenChange={(open) => { if (!open) setTestChannel(null) }}>
        <DialogContent className="sm:max-w-sm" onInteractOutside={(e) => e.preventDefault()}>
          <DialogHeader>
            <DialogTitle>{"测试支付"}{testChannel ? ` - ${testChannel.name}` : ""}</DialogTitle>
          </DialogHeader>
          <p className="text-xs text-muted-foreground">
            {"用该通道凭证真实下一笔单：会创建一笔「测试单」，支付后可在订单页核对回调是否置为已支付。测试单不计入余额、统计，也不占通道日限额。"}
          </p>
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
            {testResult && (
              <div className="mt-4 space-y-2 rounded-md border bg-muted/40 p-3">
                <div className="flex items-center gap-2 text-xs">
                  <span className="text-muted-foreground shrink-0">{"测试单号"}</span>
                  <span className="font-mono break-all">{testResult.trade_no}</span>
                  <Badge variant="secondary" className="shrink-0">{"测试单"}</Badge>
                </div>
                {/^https?:\/\//.test(testResult.pay_info || "") && testResult.pay_type !== "html" && (
                  <Button asChild variant="outline" size="sm" className="h-7">
                    <a href={testResult.pay_info} target="_blank" rel="noreferrer">{"打开支付页"}</a>
                  </Button>
                )}
                {(testResult.pay_type === "qrcode" || testResult.pay_type === "html") && (
                  <pre className="max-h-32 overflow-auto text-xs break-all whitespace-pre-wrap">{testResult.pay_info}</pre>
                )}
              </div>
            )}
            <DialogFooter className="mt-6">
              <Button type="button" variant="outline" onClick={() => setTestChannel(null)}>{"关闭"}</Button>
              {testResult && (
                <Button type="button" variant="secondary"
                        onClick={() => { setTestChannel(null); navigate(`/trade/orders?trade_no=${testResult.trade_no}`) }}>
                  {"查看订单"}
                </Button>
              )}
              <Button type="submit" disabled={testing}>{testResult ? "再测一笔" : "发起测试"}</Button>
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
                <p className="font-medium">{"测试请求"}</p>
                <pre className="text-xs font-mono whitespace-pre-wrap break-all max-h-32 overflow-auto bg-muted/50 rounded p-2">
                  {JSON.stringify(testDebug.request, null, 2)}
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
