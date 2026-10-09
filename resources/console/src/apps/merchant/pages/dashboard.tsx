"use client"

import * as React from "react"
import { dashboardApi } from "@/api"
import type { MerchantOverview } from "@/api/dashboard"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import {
  ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"
import { IconCoin, IconChartBar, IconWallet, IconTarget } from "@tabler/icons-react"

const trendChartConfig = {
  money: { label: "金额(元)", color: "var(--primary)" },
  count: { label: "笔数", color: "var(--chart-2)" },
} satisfies ChartConfig

function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function DashboardPage() {
  const [loading, setLoading] = React.useState(true)
  const [overview, setOverview] = React.useState<MerchantOverview | null>(null)

  React.useEffect(() => {
    dashboardApi.overview()
      .then((res) => setOverview(res as MerchantOverview))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  const yCount = overview?.yesterday.count ?? 0
  const todayCount = overview?.today.count ?? 0

  const kpis = [
    { key: "balance", label: "可用余额", icon: IconWallet, value: `¥${fmtMoney(overview?.balance ?? 0)}`, note: "账户可用余额" },
    { key: "todayCount", label: "今日订单", icon: IconChartBar, value: `${todayCount}`, note: `昨日 ${yCount} 单` },
    { key: "todayMoney", label: "今日金额", icon: IconCoin, value: fmtMoney(overview?.today.money ?? 0), note: "今日收款总金额" },
    {
      key: "successRate", label: "近7日成功率", icon: IconTarget,
      value: `${overview?.success_rate ?? "—"}%`,
      note: overview?.rate ? `${"我的费率"} ${overview.rate}%` : "近7日支付成功率",
    },
  ]

  return (
    <div className="flex flex-1 flex-col">
      <div className="@container/main flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        {/* 平台公告（发布中，仅内容） */}
        {overview && overview.announcements.length > 0 && (
          <div className="flex flex-col gap-3 px-4 lg:px-6">
            {overview.announcements.map((a) => (
              <Alert key={a.id} variant="info">
                <AlertDescription className="mt-0 whitespace-pre-wrap break-all">{a.content}</AlertDescription>
              </Alert>
            ))}
          </div>
        )}

        {/* KPI Cards */}
        <div className="grid grid-cols-1 gap-4 px-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card *:data-[slot=card]:shadow-xs lg:px-6 @xl/main:grid-cols-2 @5xl/main:grid-cols-4 dark:*:data-[slot=card]:bg-card">
          {kpis.map((kpi) => (
            <Card key={kpi.key} className="@container/card">
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
          ))}
        </div>

        {/* Trend Chart */}
        <div className="px-4 lg:px-6">
          <Card className="shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <div>
                <CardTitle className="text-base">{"近 7 日收款趋势"}</CardTitle>
              </div>
              <CardAction>
                <span className="text-xs text-muted-foreground">
                  {"今日金额"} ¥{fmtMoney(overview?.today.money ?? 0)}
                </span>
              </CardAction>
            </CardHeader>
            <CardContent className="px-2 pt-0 sm:px-4">
              <ChartContainer config={trendChartConfig} className="min-h-[160px] w-full">
                <AreaChart data={overview?.trend ?? []}>
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
        </div>
      </div>
    </div>
  )
}
