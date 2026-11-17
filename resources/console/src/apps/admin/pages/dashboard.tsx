"use client"

import * as React from "react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { cn } from "@/lib/utils"
import { dashboardApi } from "@/api"
import type { AdminOverview, DayStat, HomeConfig, RatelimitStats } from "@/api/dashboard"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { ListError } from "@/components/data-list/list-error"
import { PayIcon } from "@/components/pay-icon"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import {
  ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import {
  IconGauge, IconCoin, IconUsers, IconChartBar, IconRefresh,
  IconTrendingUp, IconCalendarStats,
} from "@tabler/icons-react"

const trendChartConfig = {
  money: { label: "金额(元)", color: "var(--primary)" },
  count: { label: "笔数", color: "var(--chart-2)" },
} satisfies ChartConfig

const RATELIMIT_LS_KEY = "ratelimit_last_channel"

/** 金额路由表单值（enable 由 channel>0 推导，不单独维护） */
interface RouteForm {
  min: string
  max: string
  channel: number
}

const EMPTY_ROUTE: RouteForm = { min: "", max: "", channel: 0 }


function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtNum(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN")
}

/** 分组小标题（今日统计内部），下划线使用官方 Separator 组件 */
function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-sm font-medium">{children}</div>
      <Separator />
    </div>
  )
}

/** 今日统计表（支付方式收入 / 通道收入 / 支付方式利润 三张共用），仅今日一行 */
function TodayTable({ dayToday, nameMap, iconMap, kind }: {
  dayToday: DayStat
  nameMap: Record<string, string>
  /** 支付方式 id → 图标 URL（仅支付方式两种统计传入） */
  iconMap?: Record<string, string>
  kind: "paytype" | "channel" | "profit"
}) {
  const ids = Object.keys(nameMap)
  const cellOf = (id: string) =>
    fmtMoney((kind === "profit" ? dayToday.profit_paytype[id] : dayToday[kind][id]) ?? 0)
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="h-8">{"日期"}</TableHead>
          {ids.map((id) => (
            <TableHead key={id} className="h-8">
              <span className="flex items-center gap-1">
                {/* 支付方式两种统计带图标；通道统计的 key 是通道 ID，不显示图标 */}
                {kind !== "channel" && <PayIcon icon={iconMap?.[id]} />}
                {nameMap[id]}
              </span>
            </TableHead>
          ))}
          <TableHead className="h-8">{"总计"}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell className="h-8 font-medium">{"今日"}</TableCell>
          {ids.map((id) => <TableCell key={id} className="h-8 tabular-nums">{cellOf(id)}</TableCell>)}
          <TableCell className="h-8 font-medium tabular-nums">
            {fmtMoney(kind === "profit" ? dayToday.profit_all : dayToday.all)}
          </TableCell>
        </TableRow>
      </TableBody>
    </Table>
  )
}

/** 今日统计表加载骨架（表头 + 数据行各一行高度） */
function TodayTableSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      <Skeleton className="h-8 w-full" />
      <Skeleton className="h-8 w-full" />
    </div>
  )
}

/** 数字滚动动画：挂载/数值变化时从 0 递增到目标值；prefers-reduced-motion 时直接显示 */
function AnimatedNumber({ value, format }: { value: number; format: (v: number) => string }) {
  const [display, setDisplay] = React.useState(0)

  React.useEffect(() => {
    const reduce = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    const target = Number.isFinite(value) ? value : 0
    const duration = reduce || target === 0 ? 0 : 600
    const start = performance.now()
    let raf = requestAnimationFrame(function tick(now) {
      const p = duration === 0 ? 1 : Math.min(1, (now - start) / duration)
      setDisplay(target * (1 - (1 - p) ** 3))
      if (p < 1) raf = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(raf)
  }, [value])

  return <>{format(display)}</>
}

function fmtRate(v: number): string {
  return `${Math.round(v * 100) / 100}%`
}

function fmtCount(v: number): string {
  return fmtNum(Math.round(v))
}

export function DashboardPage() {

  const [loading, setLoading] = React.useState(true)
  const [overview, setOverview] = React.useState<AdminOverview | null>(null)
  const [config, setConfig] = React.useState<HomeConfig | null>(null)

  // 金额路由表单：type_id -> {金额区间, 固定通道}（是否启用由 channel 是否选择推导）
  const [routeForms, setRouteForms] = React.useState<Record<number, RouteForm>>({})
  const [routeSaved, setRouteSaved] = React.useState<Record<number, RouteForm>>({})
  const [routeSaving, setRouteSaving] = React.useState(false)

  // 通道限速面板
  const [rlChannel, setRlChannel] = React.useState<string>("")
  const [rlValue, setRlValue] = React.useState<string>("")
  const [rlStats, setRlStats] = React.useState<RatelimitStats | null>(null)
  const [rlSaving, setRlSaving] = React.useState(false)

  const applyConfig = React.useCallback((cfg: HomeConfig) => {
    setConfig(cfg)
    const forms: Record<number, RouteForm> = Object.fromEntries(cfg.pay_types.map((pt) => {
      const saved = cfg.payroute[String(pt.id)]
      // 0.00 视为未配置，输入框留空用 placeholder 提示，避免满屏「0.00」
      const amount = (v?: string) => (Number(v ?? 0) > 0 ? String(v) : "")
      return [pt.id, {
        min: amount(saved?.min),
        max: amount(saved?.max),
        channel: saved?.channel ?? 0,
      }]
    }))
    setRouteForms(forms)
    setRouteSaved(forms)

    // 通道限速：恢复最后选中通道，优先 localStorage，缺失时回退数据库
    let last = ""
    try { last = localStorage.getItem(RATELIMIT_LS_KEY) ?? "" } catch { /* ignore */ }
    if (!last && cfg.ratelimit_last > 0) last = String(cfg.ratelimit_last)
    const exists = !!last && cfg.channels.some((c) => String(c.id) === last)
    const initial = exists ? last : ""
    setRlChannel(initial)
    const lim = initial ? (cfg.ratelimit[initial] ?? 0) : 0
    setRlValue(lim > 0 ? String(lim) : "")
    if (initial && lim > 0) {
      dashboardApi.ratelimitStats(Number(initial)).then(setRlStats).catch(() => setRlStats({ show: 0 }))
    } else {
      setRlStats({ show: 0 })
    }
  }, [])

  const [refreshing, setRefreshing] = React.useState(false)
  const [loadError, setLoadError] = React.useState<unknown>(null)
  const getData = React.useCallback(async () => {
    setRefreshing(true)
    try {
      const data = await dashboardApi.overview()
      setOverview(data as AdminOverview)
    } catch (err) {
      // 401 已由 api-client 跳转到登录页；其余错误要显式提示，不能把「0」当成真实统计
      toast.error(getErrorMessage(err, "统计数据刷新失败"))
    } finally {
      setRefreshing(false)
    }
  }, [])

  // 拉取并落到 state：调用方负责 loading / error（首次加载与重试共用）
  const applyAll = React.useCallback(async () => {
    const [ov, cfg] = await Promise.all([
      dashboardApi.overview(),
      dashboardApi.homeConfig(),
    ])
    setOverview(ov as AdminOverview)
    applyConfig(cfg)
  }, [applyConfig])

  // 首次加载：loading 初值为 true，这里不再重复置位（避免 effect 内同步 setState）
  React.useEffect(() => {
    let alive = true
    void (async () => {
      try {
        await applyAll()
        if (alive) setLoadError(null)
      } catch (err) {
        if (alive) setLoadError(err)
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [applyAll])

  // 失败重试：由点击触发，先回到骨架态再拉取
  const retryLoad = () => {
    setLoading(true)
    setLoadError(null)
    void (async () => {
      try {
        await applyAll()
      } catch (err) {
        setLoadError(err)
      } finally {
        setLoading(false)
      }
    })()
  }

  const trend: { date: string; count: number; money: number }[] = overview?.trend ?? []
  const kpis = [
    {
      key: "today_deduct_money", label: "今日预计收入", icon: IconTrendingUp,
      value: fmtMoney(overview?.today_deduct_money ?? 0), to: "",
      note: "今日实时汇总",
    },
    {
      key: "yesterday_deduct_money", label: "昨日预计收入", icon: IconCalendarStats,
      value: fmtMoney(overview?.yesterday_deduct_money ?? 0), to: "",
      note: "昨日全天汇总",
    },
    {
      key: "usermoney", label: "总计余额", icon: IconCoin,
      value: fmtMoney(overview?.usermoney ?? 0), to: "",
      note: "商户余额合计",
    },
    {
      key: "settlemoney", label: "结算总额", icon: IconGauge,
      value: fmtMoney(overview?.settlemoney ?? 0), to: "/settle",
      note: "已结算总金额",
    },
    {
      key: "count1", label: "订单总数", icon: IconChartBar,
      value: fmtNum(overview?.count1 ?? 0), to: "/trade/orders",
      note: "已支付总单数",
    },
    {
      key: "count2", label: "商户数量", icon: IconUsers,
      value: fmtNum(overview?.count2 ?? 0), to: "/merchants",
      note: "入驻商户总数",
    },
  ]

  // ---- 金额路由：整卡片一次保存（只提交有变更的支付方式） ----
  const routeDirty = React.useMemo(() => Object.keys(routeForms).some((id) => {
    const cur = routeForms[Number(id)]
    const saved = routeSaved[Number(id)]
    return !saved || cur.min !== saved.min || cur.max !== saved.max || cur.channel !== saved.channel
  }), [routeForms, routeSaved])

  const savePayroute = async () => {
    const changed = (config?.pay_types ?? []).filter((pt) => {
      const cur = routeForms[pt.id]
      const saved = routeSaved[pt.id]
      return cur && (!saved || cur.min !== saved.min || cur.max !== saved.max || cur.channel !== saved.channel)
    })
    if (!changed.length) return

    // 本地先校验：启用（选了通道）时必须填写完整且合法的区间，否则存下去也不会生效
    for (const pt of changed) {
      const form = routeForms[pt.id]
      if (form.channel === 0) continue
      const min = Number(form.min)
      const max = Number(form.max)
      if (!form.min.trim() || !form.max.trim() || !Number.isFinite(min) || !Number.isFinite(max)) {
        toast.error(`${pt.name}：请填写最小/最大金额`)
        return
      }
      if (min <= 0 || max <= 0) {
        toast.error(`${pt.name}：最小/最大金额都须大于 0`)
        return
      }
      if (max < min) {
        toast.error(`${pt.name}：最大金额不能小于最小金额`)
        return
      }
    }

    setRouteSaving(true)
    try {
      await dashboardApi.savePayrouteBatch(changed.map((pt) => {
        const form = routeForms[pt.id]
        return {
          type_id: pt.id, enable: form.channel > 0,
          min: form.min.trim() || "0", max: form.max.trim() || "0", channel: form.channel,
        }
      }))
      toast.success("金额路由已保存")
      applyConfig(await dashboardApi.homeConfig())
    } catch (e) {
      toast.error(getErrorMessage(e, "操作失败"))
    } finally {
      setRouteSaving(false)
    }
  }

  // ---- 通道限速 ----
  const loadRatelimit = (cid: string, map?: Record<string, number>) => {
    const lim = (map ?? config?.ratelimit ?? {})[cid] ?? 0
    setRlValue(lim > 0 ? String(lim) : "")
    if (!cid || lim <= 0) { setRlStats({ show: 0 }); return }
    setRlStats(null)
    dashboardApi.ratelimitStats(Number(cid)).then(setRlStats).catch(() => setRlStats({ show: 0 }))
  }

  const onRatelimitChannelChange = (cid: string) => {
    setRlChannel(cid)
    if (cid) {
      try { localStorage.setItem(RATELIMIT_LS_KEY, cid) } catch { /* ignore */ }
      dashboardApi.saveRatelimitLast(Number(cid)).catch(() => {})
    }
    loadRatelimit(cid)
  }

  const saveRatelimit = async () => {
    const cid = Number(rlChannel) || 0
    if (!cid) {
      // 「关闭」：一键关闭所有通道限速
      setRlSaving(true)
      try {
        await dashboardApi.saveRatelimit({ channel_id: 0 })
        try { localStorage.removeItem(RATELIMIT_LS_KEY) } catch { /* ignore */ }
        toast.success("已关闭所有通道限速")
        const cfg = await dashboardApi.homeConfig()
        applyConfig(cfg)
      } catch (e) {
        toast.error(getErrorMessage(e, "操作失败"))
      } finally {
        setRlSaving(false)
      }
      return
    }
    const val = rlValue.trim()
    if (val === "" || !/^[0-9]+$/.test(val)) {
      toast.error("请输入正确的次数（非负整数）")
      return
    }
    setRlSaving(true)
    try {
      await dashboardApi.saveRatelimit({ channel_id: cid, value: Number(val) })
      toast.success("通道限速已保存")
      const cfg = await dashboardApi.homeConfig()
      setConfig(cfg)
      loadRatelimit(String(cid), cfg.ratelimit)
    } catch (e) {
      toast.error(getErrorMessage(e, "操作失败"))
    } finally {
      setRlSaving(false)
    }
  }

  const paytypeMap = overview?.paytype ?? {}
  const paytypeIconMap = overview?.paytype_icon ?? {}
  const channelMap = overview?.channel ?? {}
  const orderToday = overview?.order_today ?? { all: 0, profit_all: 0, paytype: {}, channel: {}, profit_paytype: {} }

  return (
    <div className="flex flex-1 flex-col">
      {loadError ? (
        <div className="px-4 py-6 lg:px-6">
          <ListError error={loadError} onRetry={retryLoad} title="仪表盘数据加载失败" />
        </div>
      ) : (
      <div className="@container/main flex flex-1 flex-col gap-2">
        <div className="flex flex-col gap-4 py-4 md:gap-6 md:py-6">
          {/* 统计卡片（shadcn dashboard-01 SectionCards 样式） */}
          <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @3xl/main:grid-cols-3 @6xl/main:grid-cols-6">
            {kpis.map((kpi) => {
              const inner = (
                <Card key={kpi.key} className="@container/card bg-gradient-to-t from-primary/5 to-card shadow-xs dark:bg-card">
                  <CardHeader>
                    <CardDescription>{kpi.label}</CardDescription>
                    <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
                      {loading ? <Skeleton className="h-8 w-24" /> : kpi.value}
                    </CardTitle>
                  </CardHeader>
                  <CardFooter className="flex-col items-start text-sm">
                    <div className="line-clamp-1 flex gap-2 font-medium">
                      {kpi.note} <kpi.icon className="size-4" />
                    </div>
                  </CardFooter>
                </Card>
              )
              return kpi.to
                ? <Link key={kpi.key} to={kpi.to} className="hover:opacity-90 transition-opacity">{inner}</Link>
                : inner
            })}
          </div>

          {/* 交易统计 / 金额路由 / 通道限速 */}
          <div className="grid grid-cols-1 items-start gap-4 px-4 lg:px-6 xl:grid-cols-[2fr_1fr_1fr]">
            <Card>
              <CardHeader>
                <CardTitle>{"交易统计"}</CardTitle>
                <CardDescription>{"近期成功率与已支付笔数实时汇总"}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4 pt-1">
                <div className="@container/stats">
                  <div className="grid grid-cols-1 gap-3 @lg/stats:grid-cols-2">
                    <div className="rounded-lg border bg-muted/30 p-3">
                      <div className="text-muted-foreground text-xs">{"订单成功率"}</div>
                      <div className="mt-3 grid grid-cols-4 gap-2">
                        {[
                          { label: "今日", value: overview?.success_rate },
                          { label: "30分钟", value: overview?.success_rate_30m },
                          { label: "10分钟", value: overview?.success_rate_10m },
                          { label: "1分钟", value: overview?.success_rate_1m },
                        ].map((it, i) => (
                          <div key={it.label} className="flex flex-col items-center gap-1 text-center">
                            {loading ? <Skeleton className="h-6 w-10" /> : (
                              <span style={{ animationDelay: `${i * 60}ms` }}
                                className="animate-in text-xl leading-none font-semibold tabular-nums fade-in-0 slide-in-from-bottom-1 fill-mode-both duration-500">
                                <AnimatedNumber value={it.value ?? 0} format={fmtRate} />
                              </span>
                            )}
                            <span className="text-muted-foreground text-xs">{it.label}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="rounded-lg border bg-muted/30 p-3">
                      <div className="text-muted-foreground text-xs">{"已支付笔数"}</div>
                      <div className="mt-3 grid grid-cols-3 gap-2">
                        {[
                          { label: "30分钟", value: overview?.sucnum_30m },
                          { label: "10分钟", value: overview?.sucnum_10m },
                          { label: "1分钟", value: overview?.sucnum_1m },
                        ].map((it, i) => (
                          <div key={it.label} className="flex flex-col items-center gap-1 text-center">
                            {loading ? <Skeleton className="h-6 w-10" /> : (
                              <span style={{ animationDelay: `${i * 60}ms` }}
                                className="animate-in text-xl leading-none font-semibold tabular-nums fade-in-0 slide-in-from-bottom-1 fill-mode-both duration-500">
                                <AnimatedNumber value={it.value ?? 0} format={fmtCount} />
                                <span className="text-muted-foreground ml-0.5 text-xs font-normal">笔</span>
                              </span>
                            )}
                            <span className="text-muted-foreground text-xs">{it.label}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{"金额路由"}</CardTitle>
                <CardDescription>{"区间固定走通道"}</CardDescription>
                <CardAction>
                  <Button size="sm" className="h-8" variant={routeDirty ? "default" : "outline"}
                    disabled={loading || routeSaving || !routeDirty} onClick={savePayroute}>
                    {routeSaving ? <Spinner className="size-3.5" /> : routeDirty ? "保存" : "已保存"}
                  </Button>
                </CardAction>
              </CardHeader>
              <CardContent className="flex flex-col pt-1 @container/route">
                {loading ? (
                  <div className="flex flex-col gap-2 py-1">
                    {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
                  </div>
                ) : (config?.pay_types ?? []).map((pt) => {
                  const form = routeForms[pt.id] ?? EMPTY_ROUTE
                  const channels = (config?.channels ?? []).filter((c) => c.payment_type_id === pt.id)
                  const setForm = (patch: Partial<RouteForm>) =>
                    setRouteForms((s) => ({ ...s, [pt.id]: { ...form, ...patch } }))
                  return (
                    <div key={pt.id}
                      className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5 border-b py-2 last:border-b-0 @sm/route:grid-cols-[5rem_1fr_auto]">
                      <span className="flex min-w-20 items-center gap-1.5 truncate text-sm font-medium @sm/route:w-20 @sm/route:min-w-0"
                        title={pt.name}>
                        <PayIcon code={pt.code} icon={pt.icon} />
                        {pt.name}
                      </span>
                      {/* Radix Select 不支持空字符串 value，用 "off" 哨兵值代表「不启用」 */}
                      <Select
                        value={form.channel ? String(form.channel) : "off"}
                        onValueChange={(v) => setForm({ channel: v === "off" ? 0 : Number(v) })}
                      >
                        <SelectTrigger size="sm" className="w-full min-w-0">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="off">{"不启用"}</SelectItem>
                          {channels.map((c) => (
                            <SelectItem key={c.id} value={String(c.id)}>{c.name} (ID:{c.id})</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <div className="col-span-2 flex items-center gap-1 @sm/route:col-span-1">
                        <Input className="h-8 w-full min-w-0 flex-1 @sm/route:w-20 @sm/route:flex-none" placeholder={"最小"} inputMode="decimal" value={form.min}
                          disabled={form.channel === 0}
                          onChange={(e) => setForm({ min: e.target.value })} />
                        <span className="text-muted-foreground shrink-0">-</span>
                        <Input className="h-8 w-full min-w-0 flex-1 @sm/route:w-20 @sm/route:flex-none" placeholder={"最大"} inputMode="decimal" value={form.max}
                          disabled={form.channel === 0}
                          onChange={(e) => setForm({ max: e.target.value })} />
                      </div>
                    </div>
                  )
                })}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{"通道限速"}</CardTitle>
                <CardDescription>{"限制通道每分钟拉单次数"}</CardDescription>
                <CardAction>
                  {!loading && (config?.ratelimit[rlChannel] ?? 0) > 0 && (
                    <Badge variant="secondary">1 分钟 × {config?.ratelimit[rlChannel]}</Badge>
                  )}
                </CardAction>
              </CardHeader>
              <CardContent className="flex flex-col gap-2 pt-1">
                {loading ? (
                  <>
                    <Skeleton className="h-8 w-full" />
                    <Skeleton className="h-8 w-full" />
                  </>
                ) : (
                  <>
                    {/* Radix Select 不支持空字符串 value，用 "off" 哨兵值代表「关闭」，回调时映射回 "" */}
                    <Select
                      value={rlChannel || "off"}
                      onValueChange={(v) => onRatelimitChannelChange(v === "off" ? "" : v)}
                    >
                      <SelectTrigger size="sm" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="off">{"关闭（解除全部限速）"}</SelectItem>
                        {(config?.channels ?? []).map((c) => (
                          <SelectItem key={c.id} value={String(c.id)}>{c.name} (ID:{c.id})</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <div className="flex items-center gap-2">
                      {/* 选「关闭」时无目标通道，次/分钟无意义，禁用（保存仍可用：一键解除全部限速） */}
                      <Input className="h-8 min-w-0 flex-1" placeholder={"次/分钟"} disabled={!rlChannel}
                        value={rlValue} onChange={(e) => setRlValue(e.target.value)} />
                      <Button size="sm" className="h-8 shrink-0" disabled={rlSaving} onClick={saveRatelimit}>
                        {rlSaving ? <Spinner className="size-3.5" /> : "保存"}
                      </Button>
                    </div>
                  </>
                )}
                {rlStats?.show === 1 && (
                  <p className="text-muted-foreground text-xs">
                    {`总单 ${rlStats.allnum ?? 0} · 成功 ${rlStats.success ?? 0} · 成功率 ${rlStats.rate ?? 0}%`}
                  </p>
                )}
                {!loading && rlChannel && !rlStats && <Skeleton className="h-3.5 w-44" />}
              </CardContent>
            </Card>
          </div>

          {/* 收款趋势 + 今日统计（页面最底部，各自独占一行） */}
          <div className="grid grid-cols-1 gap-4 px-4 lg:px-6">
            <Card className="@container/card">
              <CardHeader>
                <CardTitle>{"近 7 日收款趋势"}</CardTitle>
                <CardDescription>{"近期收款金额与笔数走势"}</CardDescription>
                <CardAction>
                  {loading ? (
                    <Skeleton className="h-6 w-40" />
                  ) : (
                    <Badge variant="outline" className="text-emerald-500">
                      {`累计 ${fmtMoney(overview?.total.money ?? 0)} 元 / ${overview?.total.count ?? 0} 单`}
                    </Badge>
                  )}
                </CardAction>
              </CardHeader>
              <CardContent className="px-2 pt-0 sm:px-4">
                <ChartContainer config={trendChartConfig} className="aspect-auto h-[200px] w-full">
                  <AreaChart data={trend}>
                    <defs>
                      <linearGradient id="fillMoney" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="var(--color-money)" stopOpacity={0.8} />
                        <stop offset="95%" stopColor="var(--color-money)" stopOpacity={0.1} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid vertical={false} />
                    <XAxis dataKey="date" tickLine={false} axisLine={false} tickMargin={4} minTickGap={24} tick={{ fontSize: 11 }} />
                    <YAxis yAxisId="money" tickLine={false} axisLine={false} width={56} tick={{ fontSize: 11 }} />
                    <ChartTooltip cursor={false} content={<ChartTooltipContent />} />
                    <Area yAxisId="money" dataKey="money" type="natural" fill="url(#fillMoney)" stroke="var(--color-money)" />
                    <Area yAxisId="money" dataKey="count" type="natural" stroke="var(--color-count)" fill="none" />
                  </AreaChart>
                </ChartContainer>
              </CardContent>
            </Card>

            <Card className="gap-3 py-4">
              <CardHeader>
                <CardTitle>{"今日统计"}</CardTitle>
                <CardDescription>{"支付方式收入 / 通道收入 / 手续费利润"}</CardDescription>
                <CardAction>
                  <Button variant="outline" size="icon" className="size-7" onClick={() => getData()}
                    title="刷新" disabled={refreshing} aria-label="刷新">
                    <IconRefresh className={cn("size-3.5", refreshing && "animate-spin")} />
                  </Button>
                </CardAction>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付方式收入统计"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={paytypeMap} iconMap={paytypeIconMap} kind="paytype" />}
                </div>
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付通道收入统计"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={channelMap} kind="channel" />}
                </div>
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付方式手续费利润（已扣除通道成本）"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={paytypeMap} iconMap={paytypeIconMap} kind="profit" />}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
      )}
    </div>
  )
}
