"use client"

import * as React from "react"
import { useNavigate } from "react-router-dom"
import { merchantsApi, merchantSecurityApi } from "@/api"
import type { MerchantData } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
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
import type { ActionItem } from "@/components/data-list/"
import { z } from "zod"
import {
  IconSearch, IconRefresh, IconPlus, IconEdit,
  IconKey, IconHistory, IconDownload,
  IconDice, IconAdjustments,
  IconTrash, IconPin, IconLockPassword,
  IconList, IconReceipt,
} from "@tabler/icons-react"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { downloadBlob } from "@/lib/export"
import { FEATURE_ADMIN_EXPORT_EXCEL } from "@/lib/features"
import { toIntParam } from "@/lib/form-utils"
import apiClient from "@/lib/api-client"
import { CopyButton } from "@/components/ui/copy-button"

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

/** 排序（order 参数） */
const sortOptions = [
  { value: "", label: "商户号倒序" },
  { value: "money_desc", label: "余额倒序" },
  { value: "money_asc", label: "余额正序" },
  { value: "lasttime_desc", label: "登录时间倒序" },
  { value: "lasttime_asc", label: "登录时间正序" },
]

/** 结算方式（settle_id） */
const settleTypeOptions = [
  { value: 1, label: "支付宝" },
  { value: 2, label: "微信" },
  { value: 3, label: "QQ钱包" },
  { value: 4, label: "银行卡" },
]

/** 可点击状态徽标（点击切换状态） */
function ClickableBadge({ label, tone, title, onClick, icon }: {
  label: string
  tone: "ok" | "warn" | "bad" | "muted"
  title?: string
  onClick: () => void
  icon?: React.ReactNode
}) {
  const toneCls = {
    ok: "bg-emerald-500/10 text-emerald-600",
    warn: "bg-amber-500/10 text-amber-600",
    bad: "bg-rose-500/10 text-rose-500",
    muted: "bg-muted text-muted-foreground",
  }[tone]
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-xs font-medium transition-colors hover:opacity-80 ${toneCls}`}
    >
      {icon}
      {label}
    </button>
  )
}

const recordSchema = z.object({
  username: z.string().min(2, "用户名至少 2 位"),
  // 后端创建即 hash 入库，空密码会导致无法登录的账号
  password: z.string().min(6, "密码至少 6 位"),
  status: z.boolean(),
  pay_status: z.boolean(),
  settle_status: z.boolean(),
})

const editSchema = z.object({
  username: z.string(),
  status: z.boolean(),
  pay_status: z.boolean(),
  settle_status: z.boolean(),
})

export function MerchantsPage() {
  const navigate = useNavigate()

  // ---- 筛选 ----
  const [keyword2, setKeyword2] = React.useState("")
  const [dstatus, setDstatus] = React.useState("")
  const [sortOrder, setSortOrder] = React.useState("")
  const [orderDays, setOrderDays] = React.useState("")

  // ---- 基础弹窗 ----
  const [formOpen, setFormOpen] = React.useState(false)
  const [editingId, setEditingId] = React.useState<number | null>(null)
  const [balanceTarget, setBalanceTarget] = React.useState<MerchantData | null>(null)
  const [balanceAmount, setBalanceAmount] = React.useState("")
  const [balanceRemark, setBalanceRemark] = React.useState("")

  const [pwdTarget, setPwdTarget] = React.useState<MerchantData | null>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<MerchantData | null>(null)

  // ---- ulist 对齐弹窗 ----
  const [randomTarget, setRandomTarget] = React.useState<MerchantData | null>(null)
  const [randomEnable, setRandomEnable] = React.useState("0")
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
  // 平台RSA公钥（全局配置，由 platform_rsa_private_key 派生）
  const [platformKey, setPlatformKey] = React.useState("")
  const [advanceOpen, setAdvanceOpen] = React.useState(false)
  const [advanceDaysInput, setAdvanceDaysInput] = React.useState("30")
  const [batchRandomOpen, setBatchRandomOpen] = React.useState(false)
  const [batchRandomEnable, setBatchRandomEnable] = React.useState("1")
  const [batchRandomMin, setBatchRandomMin] = React.useState("")
  const [batchRandomMax, setBatchRandomMax] = React.useState("")

  const filterParams = React.useMemo(() => {
    const params: Record<string, unknown> = {}
    if (keyword2.trim()) params.keyword = keyword2.trim()
    if (dstatus) params.dstatus = dstatus
    if (sortOrder) params.order = sortOrder
    const days = toIntParam(orderDays)
    if (days !== undefined) params.order_days = days
    return params
  }, [keyword2, dstatus, sortOrder, orderDays])

  // ---- 行内操作（columns 引用；refresh/updateRow 由下方 useDataList 解构，
  //      仅在事件回调里执行，运行时已初始化）----
  const toggle = async (item: MerchantData, type: "user" | "pay" | "settle" | "fixed", status: number) => {
    try {
      await merchantsApi.setStatus(item.id, type, status)
      // 本地更新该行，避免整表刷新
      const field = type === "user" ? "status" : type === "pay" ? "pay_status" : type === "settle" ? "settle_status" : "fixed"
      updateRow(item.id, { [field]: status } as Partial<MerchantData>)
      toast.success("状态已更新")
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    }
  }

  const openRandomDialog = (item: MerchantData) => {
    setRandomTarget(item)
    setRandomEnable(item.random_enable ? "1" : "0")
    // 数据库存 Numeric(12,2)，回显去掉 .00 尾零（5.00 → 5）
    setRandomMin(item.random_enable ? String(Number(item.random_min)) : "")
    setRandomMax(item.random_enable ? String(Number(item.random_max)) : "")
  }

  const openRatelimitDialog = (item: MerchantData) => {
    setRatelimitTarget(item)
    setRatelimitValue(item.random_ratelimit > 0 ? String(item.random_ratelimit) : "")
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
        enableSorting: false,
        header: () => "用户名",
        cell: ({ row }) => (
          <span className="text-sm font-medium">{row.original.username}</span>
        ),
        meta: { headClassName: "w-[110px]", sticky: "left" },
      },
      {
        // 商户号
        accessorKey: "pid",
        enableSorting: false,
        header: () => "商户号",
        cell: ({ row }) => (
          <span className="text-sm font-bold tabular-nums">{row.original.pid}</span>
        ),
        meta: { headClassName: "w-[90px]" },
      },
      {
        // 余额（点击弹出充值/扣除）
        accessorKey: "balance",
        enableSorting: false,
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
        meta: { headClassName: "w-[95px]" },
      },
      {
        // 结算账号/姓名（点击弹出快捷修改；微信/QQ 带类型前缀）
        accessorKey: "settle_account",
        enableSorting: false,
        header: () => "结算账号/姓名",
        cell: ({ row }) => {
          const m = row.original
          const prefix = m.settle_type === 2 ? "WX:" : m.settle_type === 3 ? "QQ:" : ""
          return (
            <button
              type="button"
              title={"点击修改结算账号"}
              className="flex flex-col text-left text-xs hover:underline"
              onClick={() => openSettleDialog(m)}
            >
              {m.settle_account ? (
                <span>
                  {prefix && <span className="text-emerald-600">{prefix}</span>}
                  {m.settle_account}
                </span>
              ) : (
                <span className="text-muted-foreground">未设置</span>
              )}
              <span className="text-muted-foreground">{m.settle_name || ""}</span>
            </button>
          )
        },
        meta: { headClassName: "min-w-[130px]" },
      },
      {
        // 随机金额（点击弹出设置）
        accessorKey: "random_enable",
        enableSorting: false,
        header: () => "随机金额",
        cell: ({ row }) => {
          const m = row.original
          // 整数展示；最小=最大时只显示一个数（如 5.00 ~ 5.00 → 5）
          const label = m.random_enable
            ? (Number(m.random_min) === Number(m.random_max)
              ? String(Number(m.random_min))
              : `${Number(m.random_min)} ~ ${Number(m.random_max)}`)
            : "未开启"
          return (
            <button
              type="button"
              title={"点击设置随机金额"}
              className={`text-sm hover:underline ${m.random_enable ? "font-bold tabular-nums text-blue-600" : "text-muted-foreground"}`}
              onClick={() => openRandomDialog(m)}
            >
              {label}
            </button>
          )
        },
        meta: { headClassName: "w-[110px] text-center", cellClassName: "text-center" },
      },
      {
        // 建单限流（点击弹出设置）：同一付款 IP 每分钟建单上限，0=不限
        accessorKey: "random_ratelimit",
        enableSorting: false,
        header: () => "限流",
        cell: ({ row }) => {
          const m = row.original
          return (
            <button
              type="button"
              title={"点击设置建单限流（同一付款 IP 每分钟建单上限，0=不限）"}
              className={`text-sm hover:underline ${m.random_ratelimit > 0 ? "font-bold tabular-nums text-blue-600" : "text-muted-foreground"}`}
              onClick={() => openRatelimitDialog(m)}
            >
              {m.random_ratelimit > 0 ? m.random_ratelimit : "不限"}
            </button>
          )
        },
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      {
        // 用户状态：正常 / 禁用（点击切换）
        accessorKey: "status",
        enableSorting: false,
        header: () => "状态",
        cell: ({ row }) => {
          const m = row.original
          return (
            <ClickableBadge
              label={m.status === 1 ? "正常" : "禁用"}
              tone={m.status === 1 ? "ok" : "bad"}
              onClick={() => toggle(m, "user", m.status === 1 ? 0 : 1)}
            />
          )
        },
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      {
        // 支付权限：待审核 / 开启 / 关闭（点击循环切换）
        accessorKey: "pay_status",
        enableSorting: false,
        header: () => "支付",
        cell: ({ row }) => {
          const m = row.original
          return (
            <ClickableBadge
              label={m.pay_status === 2 ? "待审核" : m.pay_status === 1 ? "开启" : "关闭"}
              tone={m.pay_status === 2 ? "warn" : m.pay_status === 1 ? "ok" : "bad"}
              title={"待审核→开启；开启→关闭；关闭→开启"}
              onClick={() => toggle(m, "pay", m.pay_status === 2 ? 1 : m.pay_status === 1 ? 0 : 1)}
            />
          )
        },
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      {
        // 结算权限：开启 / 关闭（点击切换），关闭=能收款但余额冻结不可提
        accessorKey: "settle_status",
        enableSorting: false,
        header: () => "结算",
        cell: ({ row }) => {
          const m = row.original
          return (
            <ClickableBadge
              label={m.settle_status === 1 ? "开启" : "关闭"}
              tone={m.settle_status === 1 ? "ok" : "bad"}
              title={"关闭后仍可收款，但结算/提现被冻结"}
              onClick={() => toggle(m, "settle", m.settle_status === 1 ? 0 : 1)}
            />
          )
        },
        meta: { headClassName: "w-[70px] text-center", cellClassName: "text-center" },
      },
      {
        // 固定（点击切换）：固定后该商户不受批量随机金额影响
        accessorKey: "fixed",
        enableSorting: false,
        header: () => "固定",
        cell: ({ row }) => {
          const m = row.original
          return (
            <ClickableBadge
              label={m.fixed ? "固定" : "未固定"}
              tone={m.fixed ? "ok" : "muted"}
              title={"固定后该商户不受批量随机金额影响，再次点击取消"}
              icon={m.fixed ? <IconPin className="size-3" /> : undefined}
              onClick={() => toggle(m, "fixed", m.fixed ? 0 : 1)}
            />
          )
        },
        meta: { headClassName: "w-[80px] text-center", cellClassName: "text-center" },
      },
      actionsColumn([
        { label: "API密钥", onClick: (item) => openDetail(item) },
        { label: "调整余额", onClick: (item) => { setBalanceTarget(item); setBalanceAmount(""); setBalanceRemark("") } },
        { label: "登录", title: "SSO 一键登录商户后台", onClick: (item) => submitSso(item) },
        { label: "资金明细", icon: <IconHistory className="size-4" />, placement: "menu", title: "资金流水", onClick: (item) => navigate(`/records?merchant_id=${item.id}`) },
        { label: "交易订单", icon: <IconList className="size-4" />, placement: "menu", onClick: (item) => navigate(`/trade/orders?merchant_id=${item.id}`) },
        { label: "结算", icon: <IconReceipt className="size-4" />, placement: "menu", onClick: (item) => navigate(`/settle?merchant_id=${item.id}`) },
        { label: "重置密码", icon: <IconLockPassword className="size-4" />, placement: "menu", onClick: (item) => setPwdTarget(item) },
        { label: "编辑", icon: <IconEdit className="size-4" />, placement: "menu", onClick: (item) => { setEditingId(item.id); setFormOpen(true) } },
        { label: "删除", icon: <IconTrash className="size-4" />, placement: "menu", variant: "destructive", title: "删除商户", onClick: (item) => setDeleteTarget(item) },
      ] as ActionItem<MerchantData>[], { width: 240 }),
    ],
  })

  const handleReset = () => {
    setKeyword("")
    setKeyword2("")
    setDstatus("")
    setSortOrder("")
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
    if (randomEnable === "1" && (!randomMin || !randomMax)) {
      toast.error("若最小与最大设为相同值，买家实付金额将固定为该值（如都填 5 则固定 5 元）"); return
    }
    if (randomEnable === "1" && (!Number.isInteger(Number(randomMin)) || !Number.isInteger(Number(randomMax)))) {
      toast.error("随机金额仅支持整数"); return
    }
    try {
      await merchantsApi.setRandom(randomTarget.id, {
        random_enable: randomEnable === "1",
        random_min: randomMin || "0",
        random_max: randomMax || "0",
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
    if (batchRandomEnable === "1" && (!batchRandomMin || !batchRandomMax)) {
      toast.error("若最小与最大设为相同值，买家实付金额将固定为该值（如都填 5 则固定 5 元）"); return
    }
    if (batchRandomEnable === "1" && (!Number.isInteger(Number(batchRandomMin)) || !Number.isInteger(Number(batchRandomMax)))) {
      toast.error("随机金额仅支持整数"); return
    }
    if (!window.confirm("此操作将应用于所有未固定的商户，确定继续？")) return
    try {
      const msg = await merchantsApi.setAllRandom({
        random_enable: batchRandomEnable === "1",
        random_min: batchRandomMin || "0",
        random_max: batchRandomMax || "0",
      })
      toast.success(String(msg || "批量设置完成"))
      setBatchRandomOpen(false)
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
    // 失败时抛出：由 ConfirmDialog 提示后端原因并保持弹窗打开
    const res = await merchantSecurityApi.resetPassword(pwdTarget.id)
    toast.success(`${"新密码"}: ${res.password}`, { duration: 15000 })
    setPwdTarget(null)
    refresh()
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
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 items-end">
            <div className="col-span-1">
              <Input
                placeholder={"用户名/商户号"}
                className="h-9"
                value={keyword2}
                onChange={(e) => setKeyword2(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
              />
            </div>
            <Select value={dstatus || "_all"} onValueChange={(v) => setDstatus(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {dstatusOptions.map((o) => (
                  <SelectItem key={o.value || "_all"} value={o.value || "_all"}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={sortOrder || "_all"} onValueChange={(v) => setSortOrder(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-9 w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {sortOptions.map((o) => (
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
              {FEATURE_ADMIN_EXPORT_EXCEL && (
                <Button variant="outline" size="sm" onClick={handleExport}>
                  <IconDownload className="size-4" />
                  {"导出 Excel"}
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => { setBatchRandomEnable("1"); setBatchRandomMin(""); setBatchRandomMax(""); setBatchRandomOpen(true) }}>
                <IconDice className="size-4" />
                {"批量随机金额"}
              </Button>
            </div>
            <Button size="sm" onClick={() => { setEditingId(null); setFormOpen(true) }}>
              <IconPlus className="size-4" />
              {"新增商户"}
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

      {/* 新增商户 */}
      <FormDialog
        open={formOpen && editingId === null}
        onOpenChange={setFormOpen}
        editingId={null}
        schema={recordSchema}
        width="sm:max-w-md"
        fields={[
          { type: "input", name: "username", label: "用户名", placeholder: "用户名", span: "full", required: true, props: { autoComplete: "off" } },
          { type: "password", name: "password", label: "密码", placeholder: "密码", span: "full", required: true, props: { autoComplete: "new-password" } },
          {
            type: "switch-group", name: "switch_group", span: "full",
            items: [
              { name: "status", label: "商户状态" },
              { name: "pay_status", label: "支付权限" },
              { name: "settle_status", label: "结算权限" },
            ],
          },
        ]}
        defaultValues={() => ({
          username: "", password: "",
          status: true, pay_status: true, settle_status: true,
        })}
        onSubmit={async (_id, values) => {
          await merchantsApi.create({
            username: values.username,
            password: values.password,
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
        width="sm:max-w-md"
        fields={[
          { type: "input", name: "username", label: "用户名", placeholder: "用户名", span: "full", props: { disabled: true } },
          {
            type: "switch-group", name: "switch_group", span: "full",
            items: [
              { name: "status", label: "商户状态" },
              { name: "pay_status", label: "支付权限" },
              { name: "settle_status", label: "结算权限" },
            ],
          },
        ]}
        defaultValues={() => ({
          username: "",
          status: true, pay_status: true, settle_status: true,
        })}
        fetchDetail={(id) => merchantsApi.get(id).then((d) => ({
          username: d.username,
          status: d.status === 1,
          pay_status: d.pay_status === 1,
          settle_status: d.settle_status === 1,
        }) as unknown as Record<string, unknown>)}
        onSubmit={async (id, values) => {
          if (id) await merchantsApi.update(id, {
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
      <Dialog open={!!balanceTarget} onOpenChange={(open) => !open && setBalanceTarget(null)}>
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
              <Input
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

      {/* 重置登录密码 */}
      <ConfirmDialog
        open={!!pwdTarget}
        onOpenChange={(open) => { if (!open) setPwdTarget(null) }}
        title={"重置密码"}
        description={"确认重置该商户的登录密码？将生成随机密码且现有登录会话全部失效"}
        variant="danger"
        cancelLabel={"取消"}
        confirmLabel={"确定"}
        onConfirm={submitResetPwd}
      />

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
      <Dialog open={!!randomTarget} onOpenChange={(open) => !open && setRandomTarget(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"随机金额"} - {randomTarget?.username}</DialogTitle>
            <DialogDescription>{"若最小与最大设为相同值，买家实付金额将固定为该值（如都填 5 则固定 5 元）"}</DialogDescription>
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
            {randomEnable === "1" && (
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                <Input type="number" min={1} step={1} value={randomMin} onChange={(e) => setRandomMin(e.target.value)} placeholder={"最小金额（整数）"} />
                <span>~</span>
                <Input type="number" min={1} step={1} value={randomMax} onChange={(e) => setRandomMax(e.target.value)} placeholder={"最大金额（整数）"} />
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRandomTarget(null)}>{"取消"}</Button>
            <Button onClick={submitRandom}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 限流（单个） */}
      <Dialog open={!!ratelimitTarget} onOpenChange={(open) => !open && setRatelimitTarget(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"限流"} - {ratelimitTarget?.username}</DialogTitle>
            <DialogDescription>{"同一付款人 IP 1 分钟内最多为该商户创建的订单数，填 0 或留空表示不限制。用于防止收款链接被爬虫/脚本刷单"}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label>{"限制拉单数量"}</Label>
            <Input type="number" min={0} step={1} value={ratelimitValue} onChange={(e) => setRatelimitValue(e.target.value)} placeholder="0" />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRatelimitTarget(null)}>{"取消"}</Button>
            <Button onClick={submitRatelimit}>{"确定"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 修改结算账号 */}
      <Dialog open={!!settleTarget} onOpenChange={(open) => !open && setSettleTarget(null)}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"修改结算账号"} - {settleTarget?.username}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid gap-2">
              <Label>{"结算方式"}</Label>
              <Select value={settleTypeValue} onValueChange={setSettleTypeValue}>
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
      <Dialog open={advanceOpen} onOpenChange={setAdvanceOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"高级搜索"}</DialogTitle>
          </DialogHeader>
          <div className="flex items-center gap-2">
            <span className="text-sm whitespace-nowrap">{"超过"}</span>
            <Input type="number" min={1} value={advanceDaysInput} onChange={(e) => setAdvanceDaysInput(e.target.value)} className="w-24" placeholder={"请输入天数"} />
            <span className="text-sm">{"天无订单的用户"}</span>
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
      <Dialog open={batchRandomOpen} onOpenChange={setBatchRandomOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{"批量随机金额"}</DialogTitle>
            <DialogDescription>{"若最小与最大设为相同值，买家实付金额将固定为该值（如都填 5 则固定 5 元）"}</DialogDescription>
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
            {batchRandomEnable === "1" && (
              <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                <Input type="number" min={1} step={1} value={batchRandomMin} onChange={(e) => setBatchRandomMin(e.target.value)} placeholder={"最小金额（整数）"} />
                <span>~</span>
                <Input type="number" min={1} step={1} value={batchRandomMax} onChange={(e) => setBatchRandomMax(e.target.value)} placeholder={"最大金额（整数）"} />
              </div>
            )}
            <p className="text-xs text-amber-600">{"此操作将应用于所有未固定的商户，确定继续？"}</p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBatchRandomOpen(false)}>{"取消"}</Button>
            <Button onClick={submitBatchRandom}>{"确定"}</Button>
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
