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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
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
} from "@tabler/icons-react"

const trendChartConfig = {
  money: { label: "金额(元)", color: "var(--primary)" },
  count: { label: "笔数", color: "var(--chart-2)" },
} satisfies ChartConfig

const RATELIMIT_LS_KEY = "ratelimit_last_channel"


function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtNum(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN")
}

/** 统计行：标签 + 值（行间分割线由官方 Separator 组件渲染） */
function StatRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 py-2 text-sm">
      <span className="text-muted-foreground shrink-0">{label}</span>
      <span className="font-medium tabular-nums">{children}</span>
    </div>
  )
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
function TodayTable({ dayToday, nameMap, kind }: {
  dayToday: DayStat
  nameMap: Record<string, string>
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
          {ids.map((id) => <TableHead key={id} className="h-8">{nameMap[id]}</TableHead>)}
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

export function DashboardPage() {

  const [loading, setLoading] = React.useState(true)
  const [overview, setOverview] = React.useState<AdminOverview | null>(null)
  const [config, setConfig] = React.useState<HomeConfig | null>(null)

  // 金额路由表单：type_id -> 表单值
  const [routeForms, setRouteForms] = React.useState<Record<number, { enable: boolean; min: string; max: string; channel: number }>>({})
  const [routeSaving, setRouteSaving] = React.useState<number | null>(null)

  // 通道限速面板
  const [rlChannel, setRlChannel] = React.useState<string>("")
  const [rlValue, setRlValue] = React.useState<string>("")
  const [rlStats, setRlStats] = React.useState<RatelimitStats | null>(null)
  const [rlSaving, setRlSaving] = React.useState(false)

  const applyConfig = React.useCallback((cfg: HomeConfig) => {
    setConfig(cfg)
    setRouteForms(Object.fromEntries(cfg.pay_types.map((pt) => {
      const saved = cfg.payroute[String(pt.id)]
      return [pt.id, {
        enable: saved?.enable === 1,
        min: saved?.min ?? "",
        max: saved?.max ?? "",
        channel: saved?.channel ?? 0,
      }]
    })))

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
      key: "count1", label: "订单总数", icon: IconChartBar,
      value: fmtNum(overview?.count1 ?? 0), to: "/trade/orders",
      note: "累计支付成功订单",
    },
    {
      key: "count2", label: "商户数量", icon: IconUsers,
      value: fmtNum(overview?.count2 ?? 0), to: "/merchants",
      note: "入驻商户总数",
    },
    {
      key: "usermoney", label: "总计余额", icon: IconCoin,
      value: fmtMoney(overview?.usermoney ?? 0), to: "",
      note: "商户余额合计",
    },
    {
      key: "settlemoney", label: "结算总额", icon: IconGauge,
      value: fmtMoney(overview?.settlemoney ?? 0), to: "/settle",
      note: "累计已结算金额",
    },
  ]

  // ---- 金额路由保存 ----
  const savePayroute = async (typeId: number) => {
    const form = routeForms[typeId]
    if (!form) return
    setRouteSaving(typeId)
    try {
      await dashboardApi.savePayroute({
        type_id: typeId, enable: form.enable,
        min: form.min || "0", max: form.max || "0", channel: Number(form.channel) || 0,
      })
      toast.success("金额路由已保存")
    } catch (e) {
      toast.error(getErrorMessage(e, "操作失败"))
    } finally {
      setRouteSaving(null)
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
          <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
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

          {/* 交易统计（含通道限速） + 金额路由 */}
          <div className="grid grid-cols-1 gap-4 px-4 lg:grid-cols-[2fr_1fr] lg:px-6">
            <Card>
              <CardHeader>
                <CardTitle>{"交易统计"}</CardTitle>
                <CardDescription>{"收入与成功率实时汇总"}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col py-1">
                <div className="flex flex-wrap items-center gap-x-6">
                  <StatRow label={`${"今日预计收入"}：`}>
                    {loading ? <Skeleton className="h-4 w-24" /> : <>{fmtMoney(overview?.today_deduct_money ?? 0)} {"元"}</>}
                  </StatRow>
                  <StatRow label={`${"昨日预计收入"}：`}>
                    {loading ? <Skeleton className="h-4 w-24" /> : <>{fmtMoney(overview?.yesterday_deduct_money ?? 0)} {"元"}</>}
                  </StatRow>
                </div>
                <Separator />
                <StatRow label={`${"近期订单成功率"}：`}>
                  {loading ? <Skeleton className="h-4 w-40" /> : (
                    <>
                      <span className="text-muted-foreground font-normal">今日：</span><b>{overview?.success_rate ?? 0} %</b>，<span className="text-muted-foreground font-normal">30分钟</span> <b>{overview?.success_rate_30m ?? 0} %</b>，<span className="text-muted-foreground font-normal">10分钟</span> <b>{overview?.success_rate_10m ?? 0} %</b>，<span className="text-muted-foreground font-normal">1分钟</span> <b>{overview?.success_rate_1m ?? 0} %</b>
                    </>
                  )}
                </StatRow>
                <Separator />
                <StatRow label={`${"已支付成功笔数"}：`}>
                  {loading ? <Skeleton className="h-4 w-40" /> : (
                    <>
                      <span className="text-muted-foreground font-normal">30分钟</span> <b>{fmtNum(overview?.sucnum_30m ?? 0)}</b> 笔，<span className="text-muted-foreground font-normal">10分钟</span> <b>{fmtNum(overview?.sucnum_10m ?? 0)}</b> 笔，<span className="text-muted-foreground font-normal">1分钟</span> <b>{fmtNum(overview?.sucnum_1m ?? 0)}</b> 笔
                    </>
                  )}
                </StatRow>
                <Separator />
                {/* 通道限速（并入交易统计卡片） */}
                <div className="mt-3 flex flex-col gap-1.5 py-2">
                  <div className="text-sm font-medium">
                    {"通道限速"}
                    {(config?.ratelimit[rlChannel] ?? 0) > 0 && (
                      <span className="text-muted-foreground font-normal">（1分钟 × {config?.ratelimit[rlChannel]}）</span>
                    )}
                  </div>
                  <p className="text-muted-foreground text-xs">{"限制通道每分钟拉单次数，选择「关闭」一键全部解除"}</p>
                  {loading ? (
                    <Skeleton className="h-8 w-full" />
                  ) : (
                    <div className="flex items-center gap-2">
                      {/* Radix Select 不支持空字符串 value，用 "off" 哨兵值代表「关闭」，回调时映射回 "" */}
                      <Select
                        value={rlChannel || "off"}
                        onValueChange={(v) => onRatelimitChannelChange(v === "off" ? "" : v)}
                      >
                        <SelectTrigger size="sm" className="w-50 shrink-0">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="off">{"关闭"}</SelectItem>
                          {(config?.channels ?? []).map((c) => (
                            <SelectItem key={c.id} value={String(c.id)}>{c.name} (ID:{c.id})</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {/* 选「关闭」时无目标通道，次/分钟无意义，禁用（保存仍可用：一键解除全部限速） */}
                      <Input className="h-8 w-24 shrink-0" placeholder={"次/分钟"} disabled={!rlChannel}
                        value={rlValue} onChange={(e) => setRlValue(e.target.value)} />
                      <Button size="sm" className="h-8 shrink-0" disabled={rlSaving} onClick={saveRatelimit}>
                        {rlSaving ? <Spinner className="size-3.5" /> : "保存"}
                      </Button>
                    </div>
                  )}
                  {rlStats?.show === 1 ? (
                    <p className="text-muted-foreground text-xs">
                      {`总单 ${rlStats.allnum ?? 0}，成功 ${rlStats.success ?? 0}，成功率 ${rlStats.rate ?? 0}%`}
                    </p>
                  ) : (!loading && rlChannel && !rlStats) ? (
                    <Skeleton className="h-3.5 w-44" />
                  ) : null}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{"金额路由"}</CardTitle>
                <CardDescription>{"按支付方式设置金额区间与固定支付通道"}</CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-6 pt-2">
                {loading ? (
                  Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="flex flex-col gap-1.5">
                      <Skeleton className="h-4 w-20" />
                      <Skeleton className="h-8 w-full" />
                      <Skeleton className="h-8 w-full" />
                    </div>
                  ))
                ) : (config?.pay_types ?? []).map((pt) => {
                  const form = routeForms[pt.id] ?? { enable: false, min: "", max: "", channel: 0 }
                  const channels = (config?.channels ?? []).filter((c) => c.payment_type_id === pt.id)
                  const setForm = (patch: Partial<typeof form>) =>
                    setRouteForms((s) => ({ ...s, [pt.id]: { ...form, ...patch } }))
                  return (
                    <div key={pt.id} className="flex flex-col gap-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium">{pt.name}</span>
                        <Switch checked={form.enable} onCheckedChange={(v) => setForm({ enable: v })} aria-label={`${"状态"} - ${pt.name}`} />
                      </div>
                      <Separator />
                      <div className="flex items-center gap-2">
                        <Select
                          value={String(form.channel)}
                          onValueChange={(v) => setForm({ channel: Number(v) })}
                        >
                          <SelectTrigger size="sm" className="min-w-0 flex-1">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="0">{"请选择通道"}</SelectItem>
                            {channels.map((c) => (
                              <SelectItem key={c.id} value={String(c.id)}>{c.name} (ID:{c.id})</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <Button variant="outline" size="sm" className="h-8 shrink-0"
                          disabled={routeSaving === pt.id}
                          onClick={() => savePayroute(pt.id)}>
                          {routeSaving === pt.id ? <Spinner className="size-3.5" /> : "保存"}
                        </Button>
                      </div>
                      <div className="flex items-center gap-2">
                        <Input className="h-8 min-w-0 flex-1" placeholder={"最小"} value={form.min}
                          disabled={form.channel === 0}
                          onChange={(e) => setForm({ min: e.target.value })} />
                        <span className="text-muted-foreground shrink-0">-</span>
                        <Input className="h-8 min-w-0 flex-1" placeholder={"最大"} value={form.max}
                          disabled={form.channel === 0}
                          onChange={(e) => setForm({ max: e.target.value })} />
                      </div>
                    </div>
                  )
                })}
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
                <ChartContainer config={trendChartConfig} className="min-h-[160px] w-full">
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
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={paytypeMap} kind="paytype" />}
                </div>
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付通道收入统计"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={channelMap} kind="channel" />}
                </div>
                <div className="flex flex-col gap-2">
                  <SectionTitle>{"支付方式手续费利润（已扣除通道成本）"}</SectionTitle>
                  {loading ? <TodayTableSkeleton /> : <TodayTable dayToday={orderToday} nameMap={paytypeMap} kind="profit" />}
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
