"use client"

import * as React from "react"
import { merchantsApi, merchantSecurityApi } from "@/api"
import type { MerchantData } from "@/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Input } from "@/components/ui/input"
import { UnitInput } from "@/components/ui/unit-input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import type { ColumnDef } from "@tanstack/react-table"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import {
  useDataList,
  FormDialog,
  ConfirmDialog,
  ListError,
  PanelError,
  actionsColumn,
} from "@/components/data-list/"
import type { ActionItem, FormFieldConfig } from "@/components/data-list/"
import { z } from "zod"
import {
  IconSearch, IconRefresh, IconPlus, IconEdit,
  IconKey, IconDownload,
  IconDice, IconAdjustments, IconPercentage,
  IconCheck, IconCopy,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { downloadBlob } from "@/lib/export"
import { EXPORT_ENABLED, MERCHANT_STATUS_ENABLED, RANDOM_AMOUNT_ENABLED } from "@/lib/flags"
import { toIntParam } from "@/lib/form-utils"
import apiClient from "@/lib/api-client"
import { CopyButton } from "@/components/ui/copy-button"
import { usePermission } from "@/hooks/use-permission"
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard"

/** 综合状态筛选（dstatus 参数） */
const dstatusOptions = [
  { value: "", label: "全部用户" },
  { value: "status_1", label: "用户状态正常" },
  { value: "status_0", label: "用户状态封禁" },
  { value: "pay_2", label: "待审核用户" },
  { value: "pay_1", label: "支付状态正常" },
  { value: "pay_0", label: "支付状态关闭" },
  { value: "settle_1", label: "结算状态正常" },
  { value: "settle_0", label: "结算状态关闭" },
]

/** 结算方式（settle_id） */
const settleTypeOptions = [
  { value: 1, label: "支付宝" },
  { value: 2, label: "微信" },
  { value: 3, label: "QQ钱包" },
  { value: 4, label: "银行卡" },
]

/** 行内开关（状态/支付/结算/配置锁定） */
function StatusSwitch({ checked, disabled, title, onCheckedChange }: {
  checked: boolean
  disabled?: boolean
  title?: string
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <Switch
      size="sm"
      checked={checked}
      disabled={disabled}
      title={title}
      onCheckedChange={onCheckedChange}
      className="mx-auto"
    />
  )
}

/** 随机金额区间校验：「整数」开启时只能是整数，否则最多两位小数 */
function validateRandomRange(min: string, max: string, integerOnly: boolean): string | null {
  const lo = Number(min)
  const hi = Number(max)
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || lo <= 0 || hi <= 0) {
    return "请填写大于 0 的随机金额"
  }
  if (integerOnly && (!Number.isInteger(lo) || !Number.isInteger(hi))) {
    return "已开启「整数」，请填写整数"
  }
  if (!integerOnly) {
    const hasCent = (n: number) => Math.abs(n * 100 - Math.round(n * 100)) > 1e-6
    if (hasCent(lo) || hasCent(hi)) return "随机金额最多两位小数"
  }
  return null
}

/** 商户专属费率（%）：留空 = 不占用（回落到通道费率），填写需 0~100 */
const rateField = z.string().trim().refine((v) => {
  if (!v) return true
  const n = Number(v)
  return Number.isFinite(n) && n >= 0 && n <= 100
}, "费率需在 0~100 之间")

const recordSchema = z.object({
  username: z.string().min(2, "用户名至少 2 位"),
  // 后端创建即 hash 入库，空密码会导致无法登录的账号
  password: z.string().min(6, "密码至少 6 位"),
  rate: rateField,
  status: z.boolean(),
  pay_status: z.boolean(),
  settle_status: z.boolean(),
})

const editSchema = z.object({
  username: z.string(),
  rate: rateField,
  status: z.boolean(),
  pay_status: z.boolean(),
  settle_status: z.boolean(),
})

/** 对接信息一行：标签 + 值 + 复制按钮（复制走 CopyButton → useCopyToClipboard）。 */
function IntegrationRow({ label, value, copyValue }: {
  label: string
  value: string
  copyValue?: string
}) {
  return (
    <div className="flex items-start gap-2 px-4 py-1">
      <span className="w-14 shrink-0 pt-0.5 text-xs text-muted-foreground">{label}</span>
      <span className="min-w-0 flex-1 break-all font-mono text-[13px]">{value || "-"}</span>
      <CopyButton
        value={copyValue ?? value}
        className="size-7"
        aria-label={`复制${label}`}
        title={`复制${label}`}
        disabled={!value}
      />
    </div>
  )
}

/**
 * 商户「对接信息」只读卡片（总后台版）。
 *
 * 与商户端 `apps/merchant/pages/security.tsx` 的卡片逐字段对齐：顺序、文案、
 * `【商户对接参数】` + `字段：值` 的整段格式都一致，运营发出去的和客户自己复制的
 * 是同一份文本。区别只有一处——后台**直接展示明文密钥**（需求方明确要求不脱敏），
 * 明文来自详情接口 `GET /admin-api/merchant/{id}`，列表接口不下发。
 *
 * 默认通道由后台「通道编码」列表指定（全平台唯一），没有就整行不展示。
 * 地址取当前访问页面的 origin。行高比标准卡片紧凑（一屏看全所有字段）。
 */
function MerchantIntegrationCard({ data, canReset, onReset }: {
  data: MerchantData
  canReset: boolean
  onReset: () => void
}) {
  const { copyToClipboard, isCopied } = useCopyToClipboard()
  const site = window.location.origin.replace(/\/+$/, "")
  // RSA2 生效时 MD5 密钥已停用，且私钥只在商户侧：与商户端一样不把失效密钥发出去
  const secretValue = data.rsa2_enable
    ? "RSA2 签名（私钥由接入方自行保管）"
    : (data.api_secret || "")
  const defaultChannel = data.default_channel
  // 展示的是**通道编码**本身（下游直接当 channel_code 传），不是通道名称。
  // 口径与商户端 security.tsx 一致。
  const channelValue = defaultChannel?.code ?? ""
  const fields: Array<[string, string]> = [
    ["商户名称", data.username],
    ["商户号", String(data.pid)],
    ["API密钥", secretValue],
    ...(channelValue ? [["默认通道", channelValue] as [string, string]] : []),
    ["下单地址", `${site}/api/orders`],
    ["查询地址", `${site}/api/orders/{trade_no}`],
    ["对接文档", `${site}/docs`],
  ]
  const integrationText = [
    "【商户对接参数】",
    ...fields.map(([label, value]) => `${label}：${value}`),
  ].join("\n")

  const copyAll = async () => {
    if (!(await copyToClipboard(integrationText))) {
      toast.error("复制失败，请手动选中文本复制")
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{"对接信息"}</CardTitle>
        <CardDescription>
          {"格式与商户端「API 信息」页一致，可直接复制发送。"}
        </CardDescription>
        <CardAction className="flex items-center gap-2">
          <Badge variant={data.rsa2_enable ? "default" : "secondary"}>
            {data.rsa2_enable ? "签名方式 RSA2" : "签名方式 MD5"}
          </Badge>
          {canReset && (
            <Button size="sm" variant="outline" onClick={onReset}>
              <IconRefresh className="size-4" />
              {"重置"}
            </Button>
          )}
          <Button size="sm" onClick={copyAll}>
            {isCopied
              ? <IconCheck className="size-4 text-green-500" />
              : <IconCopy className="size-4" />}
            {"一键复制全部信息"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="p-0 pb-1">
        {/* 「【商户对接参数】」小标题只出现在一键复制的纯文本里，页面上不重复展示 */}
        {fields.map(([label, value]) => (
          <IntegrationRow key={label} label={label} value={value} />
        ))}
      </CardContent>
    </Card>
  )
}

export function MerchantsPage() {
  const hasPermission = usePermission()

  // ---- 筛选 ----
  const [keyword2, setKeyword2] = React.useState("")
  const [dstatus, setDstatus] = React.useState("")
  const [orderDays, setOrderDays] = React.useState("")
  // 行内开关请求中的 key（`${id}:${type}`），避免重复点击
  const [pendingToggle, setPendingToggle] = React.useState<Record<string, boolean>>({})

  // ---- 基础弹窗 ----
  const [formOpen, setFormOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [balanceTarget, setBalanceTarget] = React.useState<MerchantData | null>(null)
  const [balanceAmount, setBalanceAmount] = React.useState("")
  const [balanceRemark, setBalanceRemark] = React.useState("")

  const [pwdTarget, setPwdTarget] = React.useState<MerchantData | null>(null)
  const [pwdValue, setPwdValue] = React.useState("")
  const [deleteTarget, setDeleteTarget] = React.useState<MerchantData | null>(null)

  // ---- ulist 对齐弹窗 ----
  const [randomTarget, setRandomTarget] = React.useState<MerchantData | null>(null)
  const [randomEnable, setRandomEnable] = React.useState("0")
  const [randomInteger, setRandomInteger] = React.useState(false)
  const [randomMin, setRandomMin] = React.useState("")
  const [randomMax, setRandomMax] = React.useState("")
  const [ratelimitTarget, setRatelimitTarget] = React.useState<MerchantData | null>(null)
  const [ratelimitValue, setRatelimitValue] = React.useState("")
  // 结算账号快捷修改
  const [settleTarget, setSettleTarget] = React.useState<MerchantData | null>(null)
  const [settleTypeValue, setSettleTypeValue] = React.useState("1")
  const [settleAccountValue, setSettleAccountValue] = React.useState("")
  const [settleNameValue, setSettleNameValue] = React.useState("")
  // 商户 API 密钥中心
  const [detailTarget, setDetailTarget] = React.useState<MerchantData | null>(null)
  const [detailData, setDetailData] = React.useState<MerchantData | null>(null)
  const [detailLoading, setDetailLoading] = React.useState(false)
  const [detailError, setDetailError] = React.useState<unknown>(null)
  // RSA 密钥对生成（私钥仅显示一次）
  const [rsagenTarget, setRsagenTarget] = React.useState<MerchantData | null>(null)
  const [rsaResult, setRsaResult] = React.useState<{ username: string; public_key: string; private_key: string } | null>(null)
  // 重置 MD5 签名密钥（新密钥展示一次，便于交付给商户）
  const [secretResetTarget, setSecretResetTarget] = React.useState<MerchantData | null>(null)
  const [secretResult, setSecretResult] = React.useState<{ username: string; api_secret: string } | null>(null)
  // 平台RSA公钥（全局配置，由 platform_rsa_private_key 派生）
  const [platformKey, setPlatformKey] = React.useState("")
  const [advanceOpen, setAdvanceOpen] = React.useState(false)
  const [advanceDaysInput, setAdvanceDaysInput] = React.useState("30")
  const [batchRandomOpen, setBatchRandomOpen] = React.useState(false)
  const [batchRandomEnable, setBatchRandomEnable] = React.useState("1")
  const [batchRandomInteger, setBatchRandomInteger] = React.useState(false)
  const [batchRandomMin, setBatchRandomMin] = React.useState("")
  const [batchRandomMax, setBatchRandomMax] = React.useState("")

  // ---- 扣量（商户级；没有全局参数，急停 = 批量设 0）----
  const [deductTarget, setDeductTarget] = React.useState<MerchantData | null>(null)
  const [deductEnable, setDeductEnable] = React.useState("0")
  const [deductFrequency, setDeductFrequency] = React.useState("")
  const [deductMinmoney, setDeductMinmoney] = React.useState("")
  const [batchDeductOpen, setBatchDeductOpen] = React.useState(false)
  const [batchDeductEnable, setBatchDeductEnable] = React.useState("1")
  const [batchDeductFrequency, setBatchDeductFrequency] = React.useState("")
  const [batchDeductMinmoney, setBatchDeductMinmoney] = React.useState("")

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (keyword2.trim()) params.keyword = keyword2.trim()
    if (dstatus) params.dstatus = dstatus
    const days = toIntParam(orderDays)
    if (days !== undefined) params.order_days = days
    return params
  }, [keyword2, dstatus, orderDays])

  // ---- 行内操作（columns 引用；refresh/updateRow 由下方 useDataList 解构，
  //      仅在事件回调里执行，运行时已初始化）----
  const toggle = async (item: MerchantData, type: "user" | "pay" | "settle" | "fixed", status: number) => {
    const key = `${item.id}:${type}`
    if (pendingToggle[key]) return
    setPendingToggle((prev) => ({ ...prev, [key]: true }))
    try {
      await merchantsApi.setStatus(item.id, type, status)
      // 本地更新该行，避免整表刷新
      const patch: Partial<MerchantData> = type === "fixed"
        ? { fixed: status === 1 }
        : { [type === "user" ? "status" : type === "pay" ? "pay_status" : "settle_status"]: status }
      updateRow(item.id, patch)
      toast.success("状态已更新")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setPendingToggle((prev) => {
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }

  const openRandomDialog = (item: MerchantData) => {
    setRandomTarget(item)
    setRandomEnable(item.random_enable ? "1" : "0")
    setRandomInteger(!!item.random_integer)
    // 数据库存 Numeric(12,2)，回显去掉 .00 尾零（5.00 → 5）
    setRandomMin(item.random_enable ? String(Number(item.random_min)) : "")
    setRandomMax(item.random_enable ? String(Number(item.random_max)) : "")
  }

  const openRatelimitDialog = (item: MerchantData) => {
    setRatelimitTarget(item)
    setRatelimitValue(item.random_ratelimit > 0 ? String(item.random_ratelimit) : "")
  }

  const openDeductDialog = (item: MerchantData) => {
    setDeductTarget(item)
    setDeductEnable(item.deduct_enable ? "1" : "0")
    setDeductFrequency(item.deduct_frequency > 0 ? String(item.deduct_frequency) : "")
    // 数据库存 Numeric(12,2)，回显去掉 .00 尾零（5.00 → 5）
    setDeductMinmoney(Number(item.deduct_minmoney) > 0 ? String(Number(item.deduct_minmoney)) : "")
  }

  const openSettleDialog = (item: MerchantData) => {
    setSettleTarget(item)
    setSettleTypeValue(String(item.settle_type || 1))
    setSettleAccountValue(item.settle_account || "")
    setSettleNameValue(item.settle_name || "")
  }

  const loadDetail = React.useCallback(async (id: number) => {
    setDetailLoading(true)
    setDetailError(null)
    try {
      setDetailData(await merchantsApi.get(id))
    } catch (err) {
      setDetailError(err)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const openDetail = (item: MerchantData) => {
    setDetailTarget(item)
    setDetailData(null)
    // 平台公钥为全局配置，尽力加载（失败不阻塞详情展示）
    merchantsApi.platformKey()
      .then((r) => setPlatformKey(r.public_key || ""))
      .catch(() => setPlatformKey(""))
    void loadDetail(item.id)
  }

  const {
    table, total, loading, error, setKeyword, applyFilters, refresh, updateRow,
  } = useDataList<MerchantData>({
    fetchFn: (params) => merchantsApi.list(params as never),
    filters: filterParams,
    perPage: 20,
    columns: [
      {
        // 用户名
        accessorKey: "username",
        header: () => "用户名",
        cell: ({ row }) => (
          <span className="text-sm font-medium">{row.original.username}</span>
        ),
        meta: { headClassName: "w-[120px]", sticky: "left" },
      },
      {
        // 商户号
        accessorKey: "pid",
        header: () => "商户号",
        cell: ({ row }) => (
          <span className="text-sm font-bold tabular-nums">{row.original.pid}</span>
        ),
        meta: { headClassName: "w-[110px]" },
      },
      {
        // 余额（点击弹出充值/扣除）
        accessorKey: "balance",
        header: () => "余额",
        cell: ({ row }) => (
          <button
            type="button"
            className="text-sm font-bold tabular-nums text-blue-600 hover:underline"
            onClick={() => { setBalanceTarget(row.original); setBalanceAmount(""); setBalanceRemark("") }}
          >
            ¥{row.original.balance}
          </button>
        ),
        meta: { headClassName: "w-[110px] text-right", cellClassName: "text-right" },
      },
      {
        // 商户专属费率（%）：空 = 不占用专属费率，回落到编码 / 通道费率
        accessorKey: "rate",
        enableSorting: false,
        header: () => "费率",
        cell: ({ row }) => (
          <span className="text-sm tabular-nums text-muted-foreground">
            {row.original.rate ? `${row.original.rate}%` : "按通道"}
          </span>
        ),
        meta: { headClassName: "w-[80px] text-right", cellClassName: "text-right" },
      },
      {
        // 结算账号/姓名（点击弹出快捷修改；微信/QQ 带类型前缀）
        accessorKey: "settle_account",
        enableSorting: false,
        header: () => "结算账号/姓名",
        cell: ({ row }) => {
          const m = row.original
          const prefix = m.settle_type === 2 ? "WX:" : m.settle_type === 3 ? "QQ:" : ""
          // 未设置时用小按钮占位，比灰色文字更容易发现可点击
          if (!m.settle_account) {
            return (
              <Button
                variant="outline"
                size="xs"
                title={"点击设置结算账号/姓名"}
                onClick={() => openSettleDialog(m)}
              >
                {"设置账号"}
              </Button>
            )
          }
          return (
            <button
              type="button"
              title={"点击修改结算账号"}
              className="inline-flex items-center gap-1 text-left text-sm hover:underline"
              onClick={() => openSettleDialog(m)}
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate">
                  {prefix && <span className="text-emerald-600">{prefix}</span>}
                  {m.settle_account}
                </span>
                <span className="truncate text-muted-foreground">{m.settle_name || ""}</span>
              </span>
              <IconEdit className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          )
        },
        meta: { headClassName: "w-[240px]" },
      },
      // 随机金额 / 建单限流：暂时隐藏列与设置入口（flags.ts 恢复）
      ...(RANDOM_AMOUNT_ENABLED ? ([{
        // 随机金额（点击弹出设置）
        accessorKey: "random_enable",
        enableSorting: false,
        header: () => "随机金额",
        cell: ({ row }) => {
          const m = row.original
          if (!m.random_enable) {
            return (
              <Button
                variant="outline"
                size="xs"
                title={"点击设置随机金额"}
                onClick={() => openRandomDialog(m)}
              >
                {"设置随机金额"}
              </Button>
            )
          }
          // 整数展示；最小=最大时只显示一个数（如 5.00 ~ 5.00 → 5）
          const label = Number(m.random_min) === Number(m.random_max)
            ? String(Number(m.random_min))
            : `${Number(m.random_min)} ~ ${Number(m.random_max)}`
          return (
            <button
              type="button"
              title={"点击设置随机金额"}
              className="inline-flex items-center gap-1 text-sm font-bold tabular-nums text-blue-600 hover:underline"
              onClick={() => openRandomDialog(m)}
            >
              {label}
              {m.random_integer && (
                <Badge variant="secondary" className="h-4 px-1 text-[10px]">{"整数"}</Badge>
              )}
              <IconEdit className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          )
        },
        meta: { headClassName: "w-[120px] text-center", cellClassName: "text-center" },
      }] as ColumnDef<MerchantData>[]) : []),
      {
        // 扣量（点击弹出设置）：商户级开关 + 每 N 笔扣 1 + 起扣金额
        accessorKey: "deduct_enable",
        enableSorting: false,
        header: () => "扣量",
        cell: ({ row }) => {
          const m = row.original
          if (!m.deduct_enable) {
            return (
              <Button
                variant="outline"
                size="xs"
                title={"点击设置扣量（商户级：开关 + 每 N 笔扣 1 + 起扣金额）"}
                onClick={() => openDeductDialog(m)}
              >
                {"设置扣量"}
              </Button>
            )
          }
          return (
            <button
              type="button"
              title={"点击设置扣量（商户级：开关 + 每 N 笔扣 1 + 起扣金额）"}
              className="inline-flex items-center gap-1 text-sm font-bold tabular-nums text-blue-600 hover:underline"
              onClick={() => openDeductDialog(m)}
            >
              {`每 ${m.deduct_frequency} 笔`}
              {Number(m.deduct_minmoney) > 0 && (
                <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                  {`满${Number(m.deduct_minmoney)}`}
                </Badge>
              )}
              <IconEdit className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          )
        },
        meta: { headClassName: "w-[120px] text-center", cellClassName: "text-center" },
      },
      // 建单限流：与随机金额同一开关（暂时隐藏设置入口）
      ...(RANDOM_AMOUNT_ENABLED ? ([{
        // 建单限流（点击弹出设置）：同一付款 IP 每分钟建单上限，0=不限
        accessorKey: "random_ratelimit",
        enableSorting: false,
        header: () => "限流",
        cell: ({ row }) => {
          const m = row.original
          if (!(m.random_ratelimit > 0)) {
            return (
              <Button
                variant="outline"
                size="xs"
                title={"点击设置建单限流（同一付款 IP 每分钟建单上限，0=不限）"}
                onClick={() => openRatelimitDialog(m)}
              >
                {"设置限流"}
              </Button>
            )
          }
          return (
            <button
              type="button"
              title={"点击设置建单限流（同一付款 IP 每分钟建单上限，0=不限）"}
              className="inline-flex items-center gap-1 text-sm font-bold tabular-nums text-blue-600 hover:underline"
              onClick={() => openRatelimitDialog(m)}
            >
              {m.random_ratelimit}
              <IconEdit className="size-3.5 shrink-0 text-muted-foreground" />
            </button>
          )
        },
        meta: { headClassName: "min-w-[90px] text-center", cellClassName: "text-center" },
      }] as ColumnDef<MerchantData>[]) : []),
      // 商户状态/支付/结算/配置锁定：暂时隐藏展示与设置（flags.ts 恢复）
      ...(MERCHANT_STATUS_ENABLED ? ([{
        // 用户状态：正常 / 禁用（开关实时切换）
        accessorKey: "status",
        header: () => "状态",
        cell: ({ row }) => {
          const m = row.original
          return (
            <StatusSwitch
              checked={m.status === 1}
              disabled={!!pendingToggle[`${m.id}:user`]}
              title={m.status === 1 ? "点击禁用该商户" : "点击启用该商户"}
              onCheckedChange={(v) => toggle(m, "user", v ? 1 : 0)}
            />
          )
        },
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      {
        // 支付权限：开启 / 关闭（待审核时额外提示，开关切换 开启↔关闭）
        accessorKey: "pay_status",
        header: () => "支付",
        cell: ({ row }) => {
          const m = row.original
          return (
            <div className="flex items-center justify-center gap-1.5">
              <StatusSwitch
                checked={m.pay_status === 1}
                disabled={!!pendingToggle[`${m.id}:pay`]}
                title={m.pay_status === 1 ? "点击关闭支付权限" : "点击开启支付权限"}
                onCheckedChange={(v) => toggle(m, "pay", v ? 1 : 0)}
              />
              {m.pay_status === 2 && (
                <Badge variant="secondary" className="h-4 px-1 text-[10px]">{"待审核"}</Badge>
              )}
            </div>
          )
        },
        meta: { headClassName: "w-[86px] text-center", cellClassName: "text-center" },
      },
      {
        // 结算权限：开启 / 关闭（开关实时切换），关闭=能收款但余额冻结不可提
        accessorKey: "settle_status",
        header: () => "结算",
        cell: ({ row }) => {
          const m = row.original
          return (
            <StatusSwitch
              checked={m.settle_status === 1}
              disabled={!!pendingToggle[`${m.id}:settle`]}
              title={"关闭后仍可收款，但结算/提现被冻结"}
              onCheckedChange={(v) => toggle(m, "settle", v ? 1 : 0)}
            />
          )
        },
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      {
        // 配置锁定（开关）：锁定后该商户不受批量随机金额影响
        accessorKey: "fixed",
        header: () => "配置锁定",
        cell: ({ row }) => {
          const m = row.original
          return (
            <StatusSwitch
              checked={!!m.fixed}
              disabled={!!pendingToggle[`${m.id}:fixed`]}
              title={"锁定后该商户不受批量随机金额影响"}
              onCheckedChange={(v) => toggle(m, "fixed", v ? 1 : 0)}
            />
          )
        },
        meta: { headClassName: "w-[84px] text-center", cellClassName: "text-center" },
      }] as ColumnDef<MerchantData>[]) : []),
      // 操作列：宽度按按钮数量给足（再窄会让末位按钮换行）；headStyle 同时是
      // minWidth，窄屏优先保证按钮单行
      actionsColumn([
        // 行内操作全部平铺展示（不再收进「更多(⋮)」下拉）；文案即按钮，不带图标以控制列宽
        { label: "API密钥", onClick: (item) => openDetail(item) },
        { label: "调整余额", onClick: (item) => { setBalanceTarget(item); setBalanceAmount(""); setBalanceRemark("") } },
        { label: "登录", title: "SSO 一键登录商户后台", onClick: (item) => submitSso(item) },
        { label: "重置密码", onClick: (item) => { setPwdValue(""); setPwdTarget(item) } },
        // 编辑入口恒定显示：弹窗里的状态开关受 MERCHANT_STATUS_ENABLED 控制，
        // 但「费率」不受开关约束，隐藏入口会让商户专属费率无处可填。
        { label: "编辑", onClick: (item: MerchantData) => { setEditingId(item.id); setFormOpen(true) } },
        { label: "删除", variant: "destructive", title: "删除商户", onClick: (item) => setDeleteTarget(item) },
      ] as ActionItem<MerchantData>[], { width: 420 }),
    ],
  })

  const handleReset = () => {
    setKeyword("")
    setKeyword2("")
    setDstatus("")
    setOrderDays("")
    applyFilters()
  }

  const handleExport = async () => {
    try {
      const res = await apiClient.get("/merchant/export", {
        params: filterParams, responseType: "blob",
      })
      downloadBlob(res as unknown as Blob, `merchants_${Date.now()}.xlsx`)
    } catch (err) {
      toast.error(getErrorMessage(err, "导出失败"))
    }
  }

  const submitRandom = async () => {
    if (!randomTarget) return
    if (randomEnable === "1") {
      if (!randomMin || !randomMax) {
        toast.error("请填写最小和最大金额"); return
      }
      const invalid = validateRandomRange(randomMin, randomMax, randomInteger)
      if (invalid) { toast.error(invalid); return }
    }
    try {
      await merchantsApi.setRandom(randomTarget.id, {
        random_enable: randomEnable === "1",
        random_min: randomMin || "0",
        random_max: randomMax || "0",
        random_integer: randomInteger,
      })
      toast.success("随机金额设置已保存")
      setRandomTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitRatelimit = async () => {
    if (!ratelimitTarget) return
    let v = Number(ratelimitValue)
    if (!Number.isFinite(v) || v < 0) v = 0
    try {
      await merchantsApi.setRatelimit(ratelimitTarget.id, { random_ratelimit: Math.floor(v) })
      toast.success("限流设置已保存")
      setRatelimitTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitSettle = async () => {
    if (!settleTarget) return
    if (!settleAccountValue.trim() || !settleNameValue.trim()) {
      toast.error("结算账号与姓名不能为空"); return
    }
    try {
      await merchantsApi.update(settleTarget.id, {
        settle_type: Number(settleTypeValue),
        settle_account: settleAccountValue.trim(),
        settle_name: settleNameValue.trim(),
      })
      toast.success("结算信息已更新")
      setSettleTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitBatchRandom = async () => {
    if (batchRandomEnable === "1") {
      if (!batchRandomMin || !batchRandomMax) {
        toast.error("请填写最小和最大金额"); return
      }
      const invalid = validateRandomRange(batchRandomMin, batchRandomMax, batchRandomInteger)
      if (invalid) { toast.error(invalid); return }
    }
    try {
      const msg = await merchantsApi.setAllRandom({
        random_enable: batchRandomEnable === "1",
        random_min: batchRandomMin || "0",
        random_max: batchRandomMax || "0",
        random_integer: batchRandomInteger,
      })
      toast.success(String(msg || "批量设置完成"))
      setBatchRandomOpen(false)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitDeduct = async () => {
    if (!deductTarget) return
    const freq = Number(deductFrequency)
    if (deductEnable === "1" && (!Number.isInteger(freq) || freq <= 0)) {
      toast.error("开启扣量必须填写频次（每 N 笔扣 1）"); return
    }
    const minmoney = Number(deductMinmoney || 0)
    if (!Number.isFinite(minmoney) || minmoney < 0) {
      toast.error("起扣金额不能为负数"); return
    }
    if (Math.abs(minmoney * 100 - Math.round(minmoney * 100)) > 1e-6) {
      toast.error("起扣金额最多两位小数"); return
    }
    try {
      await merchantsApi.setDeduct(deductTarget.id, {
        deduct_enable: deductEnable === "1",
        // 关闭时也带上频次：停用不销毁配置，恢复时不用重填
        ...(Number.isInteger(freq) && freq > 0 ? { deduct_frequency: freq } : {}),
        deduct_minmoney: String(minmoney),
      })
      toast.success("扣量设置已保存")
      setDeductTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitBatchDeduct = async () => {
    const freq = Number(batchDeductFrequency)
    if (batchDeductEnable === "1" && (!Number.isInteger(freq) || freq <= 0)) {
      toast.error("开启扣量必须填写频次（每 N 笔扣 1）"); return
    }
    const minmoney = Number(batchDeductMinmoney || 0)
    if (!Number.isFinite(minmoney) || minmoney < 0) {
      toast.error("起扣金额不能为负数"); return
    }
    if (Math.abs(minmoney * 100 - Math.round(minmoney * 100)) > 1e-6) {
      toast.error("起扣金额最多两位小数"); return
    }
    try {
      const msg = await merchantsApi.setAllDeduct({
        deduct_enable: batchDeductEnable === "1",
        // 关闭只写开关位：频次与金额都不下发，原样保留
        ...(batchDeductEnable === "1"
          ? { deduct_frequency: freq, deduct_minmoney: String(minmoney) }
          : {}),
      })
      toast.success(String(msg || "批量设置完成"))
      setBatchDeductOpen(false)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitSso = async (m: MerchantData) => {
    try {
      const res = await merchantsApi.sso(m.id)
      window.open(res.url, "_blank")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitBalance = async () => {
    if (!balanceTarget) return
    try {
      await merchantsApi.adjustBalance(balanceTarget.id, {
        amount: balanceAmount,
        remark: balanceRemark,
      })
      toast.success("余额已调整")
      setBalanceTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitSecretReset = async () => {
    if (!secretResetTarget) return
    const res = await merchantsApi.resetApiSecret(secretResetTarget.id)
    setDetailData((d) => (d && d.id === secretResetTarget.id
      ? {
        ...d,
        // 卡片直接吃明文：重置后立刻显示新密钥，不再只更新掩码
        api_secret: res.api_secret,
        api_secret_masked: `${res.api_secret.slice(0, 4)}****${res.api_secret.slice(-4)}`,
        has_api_secret: true,
      }
      : d))
    setSecretResult({ username: secretResetTarget.username, api_secret: res.api_secret })
    setSecretResetTarget(null)
    refresh()
  }

  const submitRsaGen = async () => {
    if (!rsagenTarget) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    const res = await merchantsApi.generateRsaKey(rsagenTarget.id)
    setDetailData((d) => (d && d.id === rsagenTarget.id ? { ...d, public_key: res.public_key } : d))
    setRsaResult({ username: rsagenTarget.username, ...res }) // 私钥仅此一次展示
    setRsagenTarget(null)
    refresh()
  }

  const submitResetPwd = async () => {
    if (!pwdTarget) return
    const pwd = pwdValue.trim()
    if (pwd.length < 6) {
      toast.error("密码至少 6 位"); return
    }
    try {
      await merchantSecurityApi.resetPassword(pwdTarget.id, pwd)
      toast.success("密码已重置，该商户现有登录会话已失效")
      setPwdTarget(null)
      refresh()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const submitDelete = async () => {
    if (!deleteTarget) return
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    await merchantsApi.remove(deleteTarget.id)
    toast.success("删除成功")
    setDeleteTarget(null)
    refresh()
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[18rem_9rem]">
            <Input
              placeholder={"用户名/商户号"}
              className="h-9"
              value={keyword2}
              onChange={(e) => setKeyword2(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
            />
            <Select
              value={dstatus || "_all"}
              onValueChange={(v) => setDstatus(v === "_all" ? "" : v)}
              items={dstatusOptions.map((o) => ({ value: o.value || "_all", label: o.label }))}
            >
              <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {dstatusOptions.map((o) => (
                  <SelectItem key={o.value || "_all"} value={o.value || "_all"}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <Button size="sm" onClick={() => applyFilters()}>
                <IconSearch className="size-4" />
                {"查询"}
              </Button>
              <Button variant="outline" size="sm" onClick={handleReset}>
                <IconRefresh className="size-4" />
                {"重置"}
              </Button>
              <Button variant="outline" size="sm" onClick={() => { setAdvanceDaysInput(orderDays || "30"); setAdvanceOpen(true) }}>
                <IconAdjustments className="size-4" />
                {"高级搜索"}
              </Button>
              {EXPORT_ENABLED && hasPermission("merchant.export") && (
                <Button variant="outline" size="sm" onClick={handleExport}>
                  <IconDownload className="size-4" />
                  {"导出 Excel"}
                </Button>
              )}
            </div>
            <div className="flex items-center gap-2">
              {RANDOM_AMOUNT_ENABLED && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setBatchRandomEnable("1")
                    setBatchRandomInteger(false)
                    setBatchRandomMin("")
                    setBatchRandomMax("")
                    setBatchRandomOpen(true)
                  }}
                >
                  <IconDice className="size-4" />
                  {"批量随机金额"}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setBatchDeductEnable("1")
                  setBatchDeductFrequency("")
                  setBatchDeductMinmoney("")
                  setBatchDeductOpen(true)
                }}
              >
                <IconPercentage className="size-4" />
                {"批量扣量"}
              </Button>
              <Button size="sm" onClick={() => { setEditingId(null); setFormOpen(true) }}>
                <IconPlus className="size-4" />
                {"新增商户"}
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

      {/* 新增商户 */}
      <FormDialog
        open={formOpen && editingId === null}
        onOpenChange={setFormOpen}
        editingId={null}
        schema={recordSchema}
        width="sm:max-w-sm"
        fields={[
          { type: "input", name: "username", label: "用户名", placeholder: "用户名", span: "full", required: true, props: { autoComplete: "off" } },
          { type: "input", name: "password", label: "密码", placeholder: "密码", span: "full", required: true, props: { autoComplete: "new-password", spellCheck: false } },
          {
            type: "input", name: "rate", label: "费率", unit: "%", span: "full",
            placeholder: "留空 = 按通道费率",
            props: { type: "number", min: 0, max: 100, step: "0.01", inputMode: "decimal" },
          },
          ...(MERCHANT_STATUS_ENABLED ? ([{
            type: "switch-group", name: "switch_group", span: "full",
            items: [
              { name: "status", label: "商户状态" },
              { name: "pay_status", label: "支付权限" },
              { name: "settle_status", label: "结算权限" },
            ],
          }] as FormFieldConfig[]) : []),
        ]}
        defaultValues={() => ({
          username: "", password: "", rate: "",
          status: true, pay_status: true, settle_status: true,
        })}
        onSubmit={async (_id, values) => {
          await merchantsApi.create({
            username: values.username,
            password: values.password,
            rate: values.rate,
            status: values.status ? 1 : 0,
            pay_status: values.pay_status ? 1 : 0,
            settle_status: values.settle_status ? 1 : 0,
          })
          refresh()
          toast.success("商户已创建")
        }}
        createTitle={"新增商户"}
      />

      {/* 编辑商户 */}
      <FormDialog
        open={formOpen && editingId !== null}
        onOpenChange={setFormOpen}
        editingId={editingId}
        schema={editSchema}
        width="sm:max-w-sm"
        fields={[
          { type: "input", name: "username", label: "用户名", placeholder: "用户名", span: "full", props: { disabled: true } },
          {
            type: "input", name: "rate", label: "费率", unit: "%", span: "full",
            placeholder: "留空 = 按通道费率",
            props: { type: "number", min: 0, max: 100, step: "0.01", inputMode: "decimal" },
          },
          ...(MERCHANT_STATUS_ENABLED ? ([{
            type: "switch-group", name: "switch_group", span: "full",
            items: [
              { name: "status", label: "商户状态" },
              { name: "pay_status", label: "支付权限" },
              { name: "settle_status", label: "结算权限" },
            ],
          }] as FormFieldConfig[]) : []),
        ]}
        defaultValues={() => ({
          username: "", rate: "",
          status: true, pay_status: true, settle_status: true,
        })}
        fetchDetail={(id) => merchantsApi.get(id).then((d) => ({
          username: d.username,
          rate: d.rate,
          status: d.status === 1,
          pay_status: d.pay_status === 1,
          settle_status: d.settle_status === 1,
        }) as unknown as Record<string, unknown>)}
        onSubmit={async (id, values) => {
          if (id) await merchantsApi.update(id, {
            // 空串 = 清除专属费率（后端显式处理），留空不会把已有值改成 0
            rate: values.rate ?? "",
            status: values.status ? 1 : 0,
            pay_status: values.pay_status ? 1 : 0,
            settle_status: values.settle_status ? 1 : 0,
          })
          refresh()
          toast.success("商户已更新")
        }}
        editTitle={"编辑商户"}
      />

      {/* 调整余额 */}
      <Dialog open={!!balanceTarget} onOpenChange={(open) => !open && setBalanceTarget(null)} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"调整余额"}</DialogTitle>
            <DialogDescription>
              {balanceTarget?.username} · {"余额"} ¥{balanceTarget?.balance}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid gap-2">
              <Label>{"余额"}</Label>
              <UnitInput
                unit="元"
                value={balanceAmount}
                onChange={(e) => setBalanceAmount(e.target.value)}
                placeholder={"金额，正数增加、负数扣减"}
              />
            </div>
            <div className="grid gap-2">
              <Label>{"备注"}</Label>
              <Input value={balanceRemark} onChange={(e) => setBalanceRemark(e.target.value)} placeholder={"备注（可选）"} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBalanceTarget(null)}>{"取消"}</Button>
            <Button onClick={submitBalance} disabled={!balanceAmount.trim()}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重置 MD5 签名密钥 */}
      <ConfirmDialog
        open={!!secretResetTarget}
        onOpenChange={(open) => { if (!open) setSecretResetTarget(null) }}
        title={"重置MD5签名密钥"}
        description={"重置后旧密钥立即失效，商户所有 MD5 签名的接入需同步更换，确定继续？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定重置"}
        onConfirm={submitSecretReset}
      />

      {/* 新 MD5 密钥（展示一次，便于转交商户） */}
      <Dialog open={!!secretResult} onOpenChange={(open) => !open && setSecretResult(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"MD5 签名密钥已重置"} - {secretResult?.username}</DialogTitle>
            <DialogDescription>
              {"商户可在「API 信息」中查看同一密钥；如为线下交付，请通过安全渠道发送并提示其立即核对。"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2">
            <Input readOnly value={secretResult?.api_secret || ""} className="min-w-0 flex-1 font-mono text-xs" />
            <CopyButton value={secretResult?.api_secret || ""} />
          </div>
          <DialogFooter>
            <Button onClick={() => setSecretResult(null)}>{"关闭"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 生成 RSA 密钥对 */}
      <ConfirmDialog
        open={!!rsagenTarget}
        onOpenChange={(open) => { if (!open) setRsagenTarget(null) }}
        title={"生成商户RSA密钥对"}
        description={"将生成新的RSA密钥对：公钥保存到平台，私钥仅显示一次请及时转交商户保存。旧公钥将被替换，确定继续？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={submitRsaGen}
      />

      {/* 商户私钥（仅显示一次） */}
      <Dialog open={!!rsaResult} onOpenChange={(open) => !open && setRsaResult(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"商户私钥（仅显示一次）"} - {rsaResult?.username}</DialogTitle>
            <DialogDescription>
              {"请及时复制保存商户私钥！当前窗口关闭后，无法再次查询商户私钥，本站也不会保存。如遗失商户私钥，可重新生成进行替换。"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-start gap-2">
            <Textarea
              readOnly
              value={rsaResult?.private_key || ""}
              rows={6}
              className="min-w-0 flex-1 resize-none field-sizing-fixed break-all font-mono text-xs"
            />
            <CopyButton value={rsaResult?.private_key || ""} />
          </div>
          <DialogFooter>
            <Button onClick={() => setRsaResult(null)}>{"我已保存，关闭"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 重置登录密码（管理员直接输入新密码） */}
      <Dialog open={!!pwdTarget} onOpenChange={(open) => { if (!open) setPwdTarget(null) }} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"重置密码"} - {pwdTarget?.username}</DialogTitle>
            <DialogDescription>{"输入新密码后立即生效，该商户现有登录会话将全部失效"}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="reset-pwd">{"新密码"}</Label>
            <Input
              id="reset-pwd"
              type="text"
              autoComplete="new-password"
              value={pwdValue}
              onChange={(e) => setPwdValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submitResetPwd() }}
              placeholder={"至少 6 位"}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPwdTarget(null)}>{"取消"}</Button>
            <Button onClick={submitResetPwd} disabled={pwdValue.trim().length < 6}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 删除商户 */}
      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}
        title={"删除商户"}
        description={"删除后商户立即无法登录与收款（有余额或待处理结算时不可删除），资金流水保留审计。确定删除？"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={submitDelete}
      />

      {/* 随机金额（单个） */}
      {RANDOM_AMOUNT_ENABLED && (
      <Dialog open={!!randomTarget} onOpenChange={(open) => !open && setRandomTarget(null)} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"随机金额"} - {randomTarget?.username}</DialogTitle>
            <DialogDescription>{"在区间内随机生成买家实付金额；关闭「整数」时支持两位小数"}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Switch
                id="random-enable"
                checked={randomEnable === "1"}
                onCheckedChange={(v) => setRandomEnable(v ? "1" : "0")}
              />
              <Label htmlFor="random-enable" className="cursor-pointer">{"随机金额"}</Label>
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-2">
              <UnitInput
                unit="元"
                type="number" min={randomInteger ? 1 : 0.01} step={randomInteger ? 1 : 0.01}
                value={randomMin} onChange={(e) => setRandomMin(e.target.value)}
                placeholder={"最小金额"}
                disabled={randomEnable !== "1"}
              />
              <span>~</span>
              <UnitInput
                unit="元"
                type="number" min={randomInteger ? 1 : 0.01} step={randomInteger ? 1 : 0.01}
                value={randomMax} onChange={(e) => setRandomMax(e.target.value)}
                placeholder={"最大金额"}
                disabled={randomEnable !== "1"}
              />
              <div className="flex items-center gap-1.5">
                <Switch
                  id="random-integer" size="sm"
                  checked={randomInteger}
                  disabled={randomEnable !== "1"}
                  onCheckedChange={setRandomInteger}
                />
                <Label htmlFor="random-integer" className="cursor-pointer">{"整数"}</Label>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{"最小与最大金额一致时为固定金额（如都填 5 则固定 5 元）"}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRandomTarget(null)}>{"取消"}</Button>
            <Button onClick={submitRandom}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      )}

      {/* 扣量（单个）：商户级开关 + 每 N 笔扣 1 + 起扣金额 */}
      <Dialog open={!!deductTarget} onOpenChange={(open) => !open && setDeductTarget(null)} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"扣量"} - {deductTarget?.username}</DialogTitle>
            <DialogDescription>
              {"开启后按成功单计数轮询抽单：命中单在后台标为已支付，商户与代理侧一律显示未支付"}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Switch
                id="deduct-enable"
                checked={deductEnable === "1"}
                onCheckedChange={(v) => setDeductEnable(v ? "1" : "0")}
              />
              <Label htmlFor="deduct-enable" className="cursor-pointer">{"扣量"}</Label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-2">
                <Label htmlFor="deduct-frequency">{"频次（每 N 笔扣 1）"}</Label>
                <Input
                  id="deduct-frequency"
                  type="number" min={1} step={1}
                  value={deductFrequency}
                  onChange={(e) => setDeductFrequency(e.target.value)}
                  placeholder={"如 10"}
                  disabled={deductEnable !== "1"}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="deduct-minmoney">{"起扣金额"}</Label>
                <UnitInput
                  id="deduct-minmoney"
                  unit="元"
                  type="number" min={0} step={0.01}
                  value={deductMinmoney}
                  onChange={(e) => setDeductMinmoney(e.target.value)}
                  placeholder={"0 = 不限金额"}
                  disabled={deductEnable !== "1"}
                />
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {"单笔订单金额 ≥ 起扣金额才参与扣量（恰好等于也扣）；0 = 不限。比较的是买家实际支付金额。每 N 笔按本商户自己的合格单独立计数，与其他商户互不影响。"}
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeductTarget(null)}>{"取消"}</Button>
            <Button onClick={submitDeduct}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 限流（单个） */}
      {RANDOM_AMOUNT_ENABLED && (
      <Dialog open={!!ratelimitTarget} onOpenChange={(open) => !open && setRatelimitTarget(null)} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"限流"} - {ratelimitTarget?.username}</DialogTitle>
            <DialogDescription>{"同一付款人 IP 1 分钟内最多为该商户创建的订单数，填 0 或留空表示不限制。用于防止收款链接被爬虫/脚本刷单"}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label>{"限制拉单数量"}</Label>
            <UnitInput unit="次/分钟" type="number" min={0} step={1} value={ratelimitValue} onChange={(e) => setRatelimitValue(e.target.value)} placeholder="0" />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRatelimitTarget(null)}>{"取消"}</Button>
            <Button onClick={submitRatelimit}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      )}

      {/* 修改结算账号 */}
      <Dialog open={!!settleTarget} onOpenChange={(open) => !open && setSettleTarget(null)} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"修改结算账号"} - {settleTarget?.username}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid gap-2">
              <Label>{"结算方式"}</Label>
              <Select
                value={settleTypeValue}
                onValueChange={setSettleTypeValue}
                items={settleTypeOptions.map((o) => ({ value: String(o.value), label: o.label }))}
              >
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {settleTypeOptions.map((o) => (
                    <SelectItem key={o.value} value={String(o.value)}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>{"结算账号"}</Label>
              <Input value={settleAccountValue} onChange={(e) => setSettleAccountValue(e.target.value)} placeholder={"收款账号"} />
            </div>
            <div className="grid gap-2">
              <Label>{"结算姓名"}</Label>
              <Input value={settleNameValue} onChange={(e) => setSettleNameValue(e.target.value)} placeholder={"收款人姓名"} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSettleTarget(null)}>{"取消"}</Button>
            <Button onClick={submitSettle} disabled={!settleAccountValue.trim() || !settleNameValue.trim()}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 高级搜索 */}
      <Dialog open={advanceOpen} onOpenChange={setAdvanceOpen} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"高级搜索"}</DialogTitle>
          </DialogHeader>
          <div className="flex items-center gap-2">
            <span className="text-sm whitespace-nowrap">{"超过"}</span>
            <UnitInput unit="天" type="number" min={1} value={advanceDaysInput} onChange={(e) => setAdvanceDaysInput(e.target.value)} className="w-28" placeholder={"请输入天数"} />
            <span className="text-sm">{"无订单的用户"}</span>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAdvanceOpen(false)}>{"取消"}</Button>
            <Button
              onClick={() => {
                const days = Number(advanceDaysInput)
                if (!days || days <= 0) { toast.error("天无订单的用户"); return }
                setOrderDays(String(days))
                setAdvanceOpen(false)
                applyFilters()
              }}
            >
              {"查询"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 批量随机金额 */}
      {RANDOM_AMOUNT_ENABLED && (
      <Dialog open={batchRandomOpen} onOpenChange={setBatchRandomOpen} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"批量随机金额"}</DialogTitle>
            <DialogDescription>{"在区间内随机生成付款金额；开启「整数」时为整数"}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Switch
                id="batch-random-enable"
                checked={batchRandomEnable === "1"}
                onCheckedChange={(v) => setBatchRandomEnable(v ? "1" : "0")}
              />
              <Label htmlFor="batch-random-enable" className="cursor-pointer">{"随机金额"}</Label>
            </div>
            <div className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-2">
              <UnitInput
                unit="元"
                type="number" min={batchRandomInteger ? 1 : 0.01} step={batchRandomInteger ? 1 : 0.01}
                value={batchRandomMin} onChange={(e) => setBatchRandomMin(e.target.value)}
                placeholder={"最小金额"}
                disabled={batchRandomEnable !== "1"}
              />
              <span>~</span>
              <UnitInput
                unit="元"
                type="number" min={batchRandomInteger ? 1 : 0.01} step={batchRandomInteger ? 1 : 0.01}
                value={batchRandomMax} onChange={(e) => setBatchRandomMax(e.target.value)}
                placeholder={"最大金额"}
                disabled={batchRandomEnable !== "1"}
              />
              <div className="flex items-center gap-1.5">
                <Switch
                  id="batch-random-integer" size="sm"
                  checked={batchRandomInteger}
                  disabled={batchRandomEnable !== "1"}
                  onCheckedChange={setBatchRandomInteger}
                />
                <Label htmlFor="batch-random-integer" className="cursor-pointer">{"整数"}</Label>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{"最小与最大金额一致时为固定金额（如都填 5 则固定 5 元）"}</p>
            <Alert variant="warning" className="px-3 py-2.5">
              <AlertDescription className="mt-0 text-xs">
                {"此操作将应用于所有未锁定的商户，锁定的商户不受影响"}
              </AlertDescription>
            </Alert>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchRandomOpen(false)}>{"取消"}</Button>
            <Button onClick={submitBatchRandom}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      )}

      {/* 批量扣量 */}
      <Dialog open={batchDeductOpen} onOpenChange={setBatchDeductOpen} disablePointerDismissal>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"批量扣量"}</DialogTitle>
            <DialogDescription>{"作用于所有未锁定的商户；开启时必须填写频次，否则开了也永不扣"}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Switch
                id="batch-deduct-enable"
                checked={batchDeductEnable === "1"}
                onCheckedChange={(v) => setBatchDeductEnable(v ? "1" : "0")}
              />
              <Label htmlFor="batch-deduct-enable" className="cursor-pointer">{"扣量"}</Label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-2">
                <Label htmlFor="batch-deduct-frequency">{"频次（每 N 笔扣 1）"}</Label>
                <Input
                  id="batch-deduct-frequency"
                  type="number" min={1} step={1}
                  value={batchDeductFrequency}
                  onChange={(e) => setBatchDeductFrequency(e.target.value)}
                  placeholder={"如 10"}
                  disabled={batchDeductEnable !== "1"}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="batch-deduct-minmoney">{"起扣金额"}</Label>
                <UnitInput
                  id="batch-deduct-minmoney"
                  unit="元"
                  type="number" min={0} step={0.01}
                  value={batchDeductMinmoney}
                  onChange={(e) => setBatchDeductMinmoney(e.target.value)}
                  placeholder={"留空 = 不限金额"}
                  disabled={batchDeductEnable !== "1"}
                />
              </div>
            </div>
            <Alert variant="warning" className="px-3 py-2.5">
              <AlertDescription className="mt-0 text-xs">
                {"此操作将应用于所有未锁定的商户，锁定的商户不受影响"}
              </AlertDescription>
            </Alert>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchDeductOpen(false)}>{"取消"}</Button>
            <Button onClick={submitBatchDeduct}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 商户 API 密钥（平台公钥 / RSA 公钥） */}
      <Dialog open={!!detailTarget} onOpenChange={(open) => !open && setDetailTarget(null)}>
        <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{"API密钥"} - {detailTarget?.username}</DialogTitle>
            <DialogDescription>{"商户号"} {detailTarget?.pid}</DialogDescription>
          </DialogHeader>
          {detailLoading ? (
            <div className="space-y-3 py-4">
              <div className="h-8 bg-muted rounded animate-pulse" />
              <div className="h-16 bg-muted rounded animate-pulse" />
            </div>
          ) : detailError ? (
            <PanelError
              error={detailError}
              onRetry={() => { if (detailTarget) void loadDetail(detailTarget.id) }}
            />
          ) : !detailData ? (
            <p className="text-sm text-muted-foreground">{"暂无数据"}</p>
          ) : (
            <div className="space-y-4">
              {/* 对接信息（明文，可整段/逐项复制）：原「MD5 签名密钥」掩码框已并入此卡片，
                  同一值不再出现两次；重置按钮与签名方式徽标挪到卡片头部 */}
              <MerchantIntegrationCard
                data={detailData}
                canReset={hasPermission("merchant.resetkey")}
                onReset={() => setSecretResetTarget(detailData)}
              />

              {/* 平台公钥（商户用于验证平台通知签名） */}
              <div className="grid gap-1.5">
                <Label>{"平台公钥"}</Label>
                {platformKey ? (
                  <div className="flex items-start gap-2">
                    <Textarea
                      readOnly
                      value={platformKey}
                      rows={3}
                      className="min-w-0 flex-1 resize-none field-sizing-fixed break-all font-mono text-xs"
                    />
                    <CopyButton value={platformKey} />
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {"未配置：请在 站点配置 中填写 平台RSA私钥 后自动派生"}
                  </p>
                )}
              </div>

              {/* RSA 公钥 */}
              <div className="grid gap-1.5">
                <div className="flex items-center justify-between">
                  <Label>{"商户RSA公钥"}</Label>
                  <Button size="sm" variant="outline" onClick={() => setRsagenTarget(detailData)}>
                    <IconKey className="size-4" />
                    {detailData.public_key ? "重置RSA密钥对" : "生成RSA密钥对"}
                  </Button>
                </div>
                {detailData.public_key ? (
                  <div className="flex items-start gap-2">
                    <Textarea
                      readOnly
                      value={detailData.public_key}
                      rows={3}
                      className="min-w-0 flex-1 resize-none field-sizing-fixed break-all font-mono text-xs"
                    />
                    <CopyButton value={detailData.public_key} />
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">{"未生成"}</p>
                )}
              </div>

            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDetailTarget(null)}>{"关闭"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

    </div>
  )
}
