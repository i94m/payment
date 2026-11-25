"use client"

import * as React from "react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { getErrorMessage } from "@/lib/error"
import { dashboardApi } from "@/api"
import type { AdminOverview, DayStat, HomeConfig } from "@/api/dashboard"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { ListError } from "@/components/data-list/list-error"
import { PayIcon } from "@/components/pay-icon"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { UnitInput } from "@/components/ui/unit-input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { TrendChart } from "@/components/trend-chart"
import {
  IconGauge, IconCoin, IconUsers, IconChartBar, IconRefresh,
  IconTrendingUp, IconCalendarStats, IconPercentage,
} from "@tabler/icons-react"

/** 首页统计自动轮询间隔（ms）与失败退避上限 */
const REFRESH_MS = 10_000
const REFRESH_MAX_MS = 4 * REFRESH_MS

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
function TodayTable({ dayToday, nameMap, metaMap, kind }: {
  dayToday: DayStat
  nameMap: Record<string, string>
  /** 支付方式 id → { code, icon }（仅支付方式两种统计传入；code 用于 icon 未配置时取内置图标） */
  metaMap?: Record<string, { code?: string; icon?: string }>
  kind: "paytype" | "channel" | "profit"
}) {
  const ids = Object.keys(nameMap)
  // 取原始数值交给 AnimatedNumber：只有金额真的变了才做滚动动画
  const cellValue = (id: string): number =>
    Number((kind === "profit" ? dayToday.profit_paytype[id] : dayToday[kind][id]) ?? 0)
  const totalValue = Number((kind === "profit" ? dayToday.profit_all : dayToday.all) ?? 0)
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="h-8">{"日期"}</TableHead>
          {ids.map((id) => (
            <TableHead key={id} className="h-8">
              <span className="flex items-center gap-1">
                {/* 支付方式两种统计带图标；通道统计的 key 是通道 ID，不显示图标 */}
                {kind !== "channel" && (
                  <PayIcon code={metaMap?.[id]?.code} icon={metaMap?.[id]?.icon} />
                )}
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
          {ids.map((id) => (
            <TableCell key={id} className="h-8 tabular-nums">
              <AnimatedNumber value={cellValue(id)} format={fmtMoney} />
            </TableCell>
          ))}
          <TableCell className="h-8 font-medium tabular-nums">
            <AnimatedNumber value={totalValue} format={fmtMoney} />
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

/** 数字滚动动画：数值变化时从「上一次的值」缓动到新值（挂载时从 0 起）。
 *  值没变就不动，避免轮询把屏幕上的数字无谓地重播一遍；prefers-reduced-motion 时直接显示。 */
function AnimatedNumber({ value, format }: { value: number; format: (v: number) => string }) {
  const target = Number.isFinite(value) ? value : 0
  const [display, setDisplay] = React.useState(0)
  // 上一次渲染到的目标值：从它出发做增量动画（不是每次都从 0 重播）
  const prevRef = React.useRef(0)

  React.useEffect(() => {
    const from = prevRef.current
    prevRef.current = target
    if (from === target) return

    const reduce = typeof window !== "undefined"
      && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches
    if (reduce) { setDisplay(target); return }

    // 变动幅度越大动画略长一点，但封顶 900ms，避免大额数字滚太久
    const duration = Math.min(900, 400 + Math.log10(Math.abs(target - from) + 1) * 250)
    const start = performance.now()
    let raf = requestAnimationFrame(function tick(now) {
      const p = Math.min(1, (now - start) / duration)
      setDisplay(from + (target - from) * (1 - (1 - p) ** 3))
      if (p < 1) raf = requestAnimationFrame(tick)
    })
    return () => cancelAnimationFrame(raf)
  }, [target])

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
  }, [])

  const [loadError, setLoadError] = React.useState<unknown>(null)
  // 在途请求去重：轮询与手动刷新可能撞在一起，只发一个请求
  const inFlightRef = React.useRef(false)

  /** 拉取首页统计；silent=轮询调用（失败不弹 toast，由调用方退避）。
   *  返回是否成功，供轮询判断失败退避（已有在途请求时按成功处理，不累加失败次数）。 */
  const getData = React.useCallback(async (silent = false): Promise<boolean> => {
    if (inFlightRef.current) return true
    inFlightRef.current = true
    try {
      const data = await dashboardApi.overview()
      setOverview(data as AdminOverview)
      // 轮询成功后自动走出加载失败态（断网恢复后无需手动重试）
      setLoadError(null)
      return true
    } catch (err) {
      // 401 已由 api-client 跳转到登录页；其余错误要显式提示，不能把「0」当成真实统计
      if (!silent) toast.error(getErrorMessage(err, "统计数据刷新失败"))
      return false
    } finally {
      inFlightRef.current = false
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

  // 自动轮询：每 10s 刷新一次统计（后端每次实时计算，不需要缓存层）
  // - 标签页不可见时跳过请求，切回可见立即补刷一次
  // - 连续失败指数退避（10s→20s→40s→上限 40s），且不弹 toast，避免断网时刷屏
  // - 递归 setTimeout 而非 setInterval：上一轮没结束不会叠加请求
  React.useEffect(() => {
    let alive = true
    let timer: number | undefined
    let running = false
    let failures = 0

    const schedule = (delay: number) => {
      if (!alive || timer !== undefined) return
      timer = window.setTimeout(() => {
        timer = undefined
        void tick()
      }, delay)
    }

    const tick = async () => {
      if (!alive || running) return
      running = true
      try {
        if (document.visibilityState === "visible") {
          const ok = await getData(true)
          failures = ok ? 0 : failures + 1
        }
      } finally {
        running = false
      }
      schedule(Math.min(REFRESH_MS * 2 ** failures, REFRESH_MAX_MS))
    }

    const onVisibility = () => {
      if (!alive || document.visibilityState !== "visible") return
      failures = 0
      if (timer !== undefined) { window.clearTimeout(timer); timer = undefined }
      if (running) schedule(REFRESH_MS)
      else void tick()
    }

    document.addEventListener("visibilitychange", onVisibility)
    schedule(REFRESH_MS)
    return () => {
      alive = false
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener("visibilitychange", onVisibility)
    }
  }, [getData])

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
  // 卡片数值保留原始 number，交给 AnimatedNumber 做变化动画（格式化在渲染时做）
  const kpis = [
    {
      key: "today_deduct_money", label: "今日预计收入", icon: IconTrendingUp,
      value: overview?.today_deduct_money ?? 0, format: fmtMoney, to: "",
      note: "今日实时汇总",
    },
    {
      key: "today_deduct_count", label: "今日扣量", icon: IconPercentage,
      value: overview?.today_deduct_count ?? 0, format: fmtCount, to: "",
      note: "今日命中扣量笔数",
    },
    {
      key: "yesterday_deduct_money", label: "昨日预计收入", icon: IconCalendarStats,
      value: overview?.yesterday_deduct_money ?? 0, format: fmtMoney, to: "",
      note: "昨日全天汇总",
    },
    {
      key: "yesterday_deduct_count", label: "昨日扣量", icon: IconPercentage,
      value: overview?.yesterday_deduct_count ?? 0, format: fmtCount, to: "",
      note: "昨日命中扣量笔数",
    },
    {
      key: "usermoney", label: "总计余额", icon: IconCoin,
      value: overview?.usermoney ?? 0, format: fmtMoney, to: "",
      note: "商户余额合计",
    },
    {
      key: "settlemoney", label: "结算总额", icon: IconGauge,
      value: overview?.settlemoney ?? 0, format: fmtMoney, to: "/settle",
      note: "已结算总金额",
    },
    {
      key: "count1", label: "订单总数", icon: IconChartBar,
      value: overview?.count1 ?? 0, format: fmtCount, to: "/trade/orders",
      note: "已支付总单数",
    },
    {
      key: "count2", label: "商户数量", icon: IconUsers,
      value: overview?.count2 ?? 0, format: fmtCount, to: "/merchants",
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

  const paytypeMap = overview?.paytype ?? {}
  const channelMap = overview?.channel ?? {}
  // 今日统计表头图标：id → { code, icon }。icon 未配置时靠 code 走内置图标库兜底
  const paytypeMetaMap = React.useMemo(() => {
    const meta: Record<string, { code?: string; icon?: string }> = {}
    for (const pt of config?.pay_types ?? []) {
      meta[String(pt.id)] = { code: pt.code, icon: pt.icon || overview?.paytype_icon?.[String(pt.id)] || "" }
    }
    return meta
  }, [config, overview])
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
          {/* 统计卡片（shadcn dashboard-01 SectionCards 样式）：8 张，大屏 4 列 × 2 行
              （收入 / 扣量成对排列，同一行的卡片口径可比） */}
          <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @3xl/main:grid-cols-3 @6xl/main:grid-cols-4">
            {kpis.map((kpi) => {
              const inner = (
                <Card key={kpi.key} className="@container/card bg-gradient-to-t from-primary/5 to-card shadow-xs dark:bg-card">
                  <CardHeader>
                    <CardDescription>{kpi.label}</CardDescription>
                    <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
                      {loading
                        ? <Skeleton className="h-8 w-24" />
                        : <AnimatedNumber value={kpi.value} format={kpi.format} />}
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

          {/* 交易统计 / 金额路由 —— 与上方统计卡片同一套栅格（4 列），
              6xl 下一行各占 2 列；窄屏各占整行，边界与上一行卡片严格对齐。 */}
          <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @3xl/main:grid-cols-3 @6xl/main:grid-cols-4">
            <Card className="@xl/main:col-span-2 @3xl/main:col-span-3 @6xl/main:col-span-2">
              <CardHeader>
                <CardTitle>{"交易统计"}</CardTitle>
                <CardDescription>{"近期成功率与已支付笔数实时汇总"}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {/* 两组统计固定并排一行；两列 + 2x2 排列，卡片再窄也不会挤爆数字 */}
                <div className="@container/stats">
                  <div className="grid grid-cols-2 gap-3">
                    {[
                      {
                        title: "订单成功率", suffix: "", items: [
                          { label: "今日", value: overview?.success_rate },
                          { label: "30分钟", value: overview?.success_rate_30m },
                          { label: "10分钟", value: overview?.success_rate_10m },
                          { label: "1分钟", value: overview?.success_rate_1m },
                        ], format: fmtRate,
                      },
                      {
                        title: "已支付笔数", suffix: "笔", items: [
                          { label: "今日", value: overview?.sucnum_today },
                          { label: "30分钟", value: overview?.sucnum_30m },
                          { label: "10分钟", value: overview?.sucnum_10m },
                          { label: "1分钟", value: overview?.sucnum_1m },
                        ], format: fmtCount,
                      },
                    ].map((group) => (
                      <div key={group.title} className="min-w-0 rounded-lg border bg-muted/30 p-3">
                        <div className="text-muted-foreground text-xs">{group.title}</div>
                        <div className="mt-3 grid grid-cols-2 gap-x-1 gap-y-3 @lg/stats:grid-cols-4 @lg/stats:gap-x-2">
                          {group.items.map((it, i) => (
                            <div key={it.label} className="flex min-w-0 flex-col items-center gap-1 text-center">
                              {loading ? <Skeleton className="h-6 w-10" /> : (
                                <span style={{ animationDelay: `${i * 60}ms` }}
                                  className="animate-in text-xl leading-none font-semibold tabular-nums whitespace-nowrap fade-in-0 slide-in-from-bottom-1 fill-mode-both duration-500">
                                  <AnimatedNumber value={it.value ?? 0} format={group.format} />
                                  {group.suffix && (
                                    <span className="text-muted-foreground ml-0.5 text-xs font-normal">{group.suffix}</span>
                                  )}
                                </span>
                              )}
                              <span className="text-muted-foreground text-xs whitespace-nowrap">{it.label}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="@xl/main:col-span-2 @3xl/main:col-span-3 @6xl/main:col-span-2">
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
              <CardContent className="px-0 pt-0">
                {loading ? (
                  <div className="flex flex-col gap-2 px-4 py-1">
                    {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-9 w-full" />)}
                  </div>
                ) : (
                  <Table className="text-xs">
                    <TableBody>
                      {(config?.pay_types ?? []).map((pt) => {
                        const form = routeForms[pt.id] ?? EMPTY_ROUTE
                        const channels = (config?.channels ?? []).filter((c) => c.payment_type_id === pt.id)
                        const setForm = (patch: Partial<RouteForm>) =>
                          setRouteForms((s) => ({ ...s, [pt.id]: { ...form, ...patch } }))
                        const off = form.channel === 0
                        return (
                          <TableRow key={pt.id} className="hover:bg-transparent">
                            <TableCell className="whitespace-nowrap py-1.5 pl-4 pr-2">
                              <span className="flex items-center gap-1.5" title={pt.name}>
                                <PayIcon code={pt.code} icon={pt.icon} />
                                <span className="text-sm font-medium">{pt.name}</span>
                              </span>
                            </TableCell>
                            <TableCell className="w-[33%] py-1.5 pr-2">
                              {/* Select 不支持空字符串 value，用 "off" 哨兵值代表「不启用」 */}
                              <Select
                                value={form.channel ? String(form.channel) : "off"}
                                onValueChange={(v) => setForm({ channel: v === "off" ? 0 : Number(v) })}
                                items={[
                                  { value: "off", label: "不启用" },
                                  ...channels.map((c) => ({ value: String(c.id), label: `${c.name} (ID:${c.id})` })),
                                ]}
                              >
                                <SelectTrigger size="sm" className="h-8 w-full min-w-0 text-xs">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="off">{"不启用"}</SelectItem>
                                  {channels.map((c) => (
                                    <SelectItem key={c.id} value={String(c.id)}>{c.name} (ID:{c.id})</SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                            </TableCell>
                            <TableCell className="py-1.5 pr-4">
                              <div className="flex items-center gap-1">
                                <UnitInput unit="元" className="h-8 min-w-0" inputClassName="h-8 px-1.5 text-xs tabular-nums" addonClassName="px-1 text-xs" placeholder={"最小"} inputMode="decimal"
                                  value={form.min} disabled={off} aria-label={`${pt.name} 最小金额`}
                                  onChange={(e) => setForm({ min: e.target.value })} />
                                <span className="text-muted-foreground shrink-0">-</span>
                                <UnitInput unit="元" className="h-8 min-w-0" inputClassName="h-8 px-1.5 text-xs tabular-nums" addonClassName="px-1 text-xs" placeholder={"最大"} inputMode="decimal"
                                  value={form.max} disabled={off} aria-label={`${pt.name} 最大金额`}
                                  onChange={(e) => setForm({ max: e.target.value })} />
                              </div>
                            </TableCell>
                          </TableRow>
                        )
                      })}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          </div>

          {/* 收款趋势（settings: dashboard_trend_enable 控制显隐，默认隐藏，开启后才展示）+ 今日统计 */}
          <div className="grid grid-cols-1 gap-4 px-4 lg:px-6">
            {overview?.trend_enable === true && (
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
                <TrendChart data={trend} />
              </CardContent>
            </Card>
            )}

            <Card className="gap-3 py-4">
              <CardHeader>
                <CardTitle>{"今日统计"}</CardTitle>
                <CardDescription>{"支付方式收入 / 通道收入 / 手续费利润"}</CardDescription>
                <CardAction>
                  <Button variant="outline" size="icon" className="size-7" onClick={() => getData()}
                    title={`刷新（每 ${REFRESH_MS / 1000} 秒自动更新）`} aria-label="刷新">
                    <IconRefresh className="size-3.5" />
                  </Button>
                </CardAction>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付方式收入统计"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={paytypeMap} metaMap={paytypeMetaMap} kind="paytype" />}
                </div>
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付通道收入统计"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={channelMap} kind="channel" />}
                </div>
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付方式手续费利润（已扣除通道成本）"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={paytypeMap} metaMap={paytypeMetaMap} kind="profit" />}
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
