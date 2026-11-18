"use client"

import * as React from "react"
import { dashboardApi } from "@/api"
import type { MerchantOverview } from "@/api/dashboard"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { PanelError } from "@/components/data-list"
import { useRequest } from "@/apps/shared/use-request"
import { TREND_CHART_CLASS, TrendChart } from "@/components/trend-chart"
import { IconCoin, IconChartBar, IconWallet, IconTarget } from "@tabler/icons-react"

function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function DashboardPage() {
  // 首页统计整块加载：失败时保留错误提示与重试，避免「静默吞错」后展示一屏 0
  // APP_MODULE 为运行时常量，overview() 的联合返回类型在此按商户端收窄
  const { data: overview, error, loading, reload } = useRequest<MerchantOverview>(
    () => dashboardApi.overview() as Promise<MerchantOverview>,
  )

  const yCount = overview?.yesterday.count ?? 0
  const todayCount = overview?.today.count ?? 0

  const kpis = [
    {
      key: "balance",
      label: "可用余额",
      icon: IconWallet,
      value: overview ? `¥${fmtMoney(overview.balance)}` : "—",
      note: "账户可用余额",
    },
    {
      key: "todayCount",
      label: "今日订单",
      icon: IconChartBar,
      value: overview ? `${todayCount}` : "—",
      note: `昨日 ${yCount} 单`,
    },
    {
      key: "todayMoney",
      label: "今日金额",
      icon: IconCoin,
      value: overview ? fmtMoney(overview.today.money) : "—",
      note: "今日收款总金额",
    },
    {
      key: "successRate",
      label: "近7日成功率",
      icon: IconTarget,
      value: overview ? (overview.success_rate == null ? "—" : `${overview.success_rate}%`) : "—",
      note: overview?.rate ? `${"我的费率"} ${overview.rate}%` : "近7日支付成功率",
    },
  ]

  const announcements = overview?.announcements ?? []

  return (
    <div className="flex flex-1 flex-col">
      <div className="@container/main flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        {/* 平台公告（发布中，仅内容） */}
        {announcements.length > 0 && (
          <div className="flex flex-col gap-3 px-4 lg:px-6">
            {announcements.map((a) => (
              <Alert key={a.id} variant="info">
                <AlertDescription className="mt-0 whitespace-pre-wrap break-all">{a.content}</AlertDescription>
              </Alert>
            ))}
          </div>
        )}

        {/* 统计加载失败：页面级错误条 + 重试 */}
        {error && <PanelError className="mx-4 lg:mx-6" error={error} onRetry={reload} />}

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

        {/* Trend Chart（与管理端首页同一张卡片：标题 + 说明 + 右上角汇总，图表高度共用 TrendChart） */}
        <div className="px-4 lg:px-6">
          <Card>
            <CardHeader>
              <CardTitle>{"近 7 日收款趋势"}</CardTitle>
              <CardDescription>{"近期收款金额与笔数走势"}</CardDescription>
              <CardAction>
                <span className="text-xs text-muted-foreground">
                  {"今日金额"} ¥{fmtMoney(overview?.today.money ?? 0)}
                </span>
              </CardAction>
            </CardHeader>
            <CardContent className="px-2 pt-0 sm:px-4">
              {loading && !overview ? (
                <Skeleton className={TREND_CHART_CLASS} />
              ) : (
                <TrendChart data={overview?.trend ?? []} />
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
