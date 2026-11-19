"use client"

import * as React from "react"
import { dashboardApi } from "@/api"
import type { MerchantOverview, MerchantProfile } from "@/api/dashboard"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { CopyableInput } from "@/components/ui/copy-button"
import { QRCode } from "@/components/ui/qrcode"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { PanelError } from "@/components/data-list"
import { useRequest } from "@/apps/shared/use-request"
import { TREND_CHART_CLASS, TrendChart } from "@/components/trend-chart"
import { IconCoin, IconChartBar, IconWallet, IconTarget, IconLink, IconQrcode } from "@tabler/icons-react"

function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function DashboardPage() {
  // 首页统计整块加载：失败时保留错误提示与重试，避免「静默吞错」后展示一屏 0
  // APP_MODULE 为运行时常量，overview() 的联合返回类型在此按商户端收窄
  const { data: overview, error, loading, reload } = useRequest<MerchantOverview>(
    () => dashboardApi.overview() as Promise<MerchantOverview>,
  )

  // 收款信息（基础资料 + 专属收款链接）：与统计块分开加载，链接失败不影响业绩展示
  const profile = useRequest<MerchantProfile>(() => dashboardApi.profile())
  const fullLink = profile.data?.link
    ? (typeof window !== "undefined" ? window.location.origin : "") + profile.data.link
    : ""
  const paypageEnabled = profile.data?.paypage_enable !== false

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

        {/* 收款信息（专属收款链接 + 基础资料；UI 对齐代理端「代理信息」卡）
            卡片内左侧资料/链接，右侧收款二维码（可下载） */}
        <div className="px-4 lg:px-6">
          <Card className="w-full @container/card shadow-sm">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <IconLink className="size-4 text-muted-foreground" />
                {"收款信息"}
              </CardTitle>
              <CardDescription>{"专属收款链接与账户资料"}</CardDescription>
            </CardHeader>
            <CardContent>
              {profile.error ? (
                <PanelError error={profile.error} onRetry={profile.reload} />
              ) : (
                <div className="grid gap-4 @[26rem]/card:grid-cols-[minmax(0,1fr)_10rem] @[26rem]/card:gap-6">
                  <div className="min-w-0 space-y-4">
                    <div className="grid grid-cols-1 gap-x-8 gap-y-4 @[20rem]/card:grid-cols-2 @[50rem]/card:grid-cols-4">
                      <InfoRow label={"商户名称"} value={profile.data?.username} loading={profile.loading} />
                      <InfoRow label={"商户号"} value={profile.data ? String(profile.data.pid) : undefined} loading={profile.loading} />
                      <InfoRow label={"创建时间"} value={profile.data?.created_at} loading={profile.loading} />
                      <InfoRow label={"最后登录"} value={profile.data?.last_login_at} loading={profile.loading} />
                    </div>

                    <Separator />

                    <div className="space-y-2">
                      <div className="text-sm font-medium">{"你的专属收款链接"}</div>
                      <p className="text-xs text-muted-foreground">
                        {"将此链接发给付款方，对方点击后输入金额即可付款。"}
                      </p>
                      {profile.loading ? (
                        <Skeleton className="h-9 w-full" />
                      ) : !paypageEnabled ? (
                        <p className="text-xs text-muted-foreground">{"平台暂未开放收款页，链接暂不可用。"}</p>
                      ) : fullLink ? (
                        <CopyableInput value={fullLink} className="w-full font-mono text-xs" />
                      ) : (
                        <p className="text-xs text-muted-foreground">{"暂未分配专属收款链接，请稍后重试。"}</p>
                      )}
                    </div>
                  </div>

                  {/* 收款二维码：与专属链接同源，扫码输入金额即可付款，支持下载 */}
                  <div className="flex flex-col items-center justify-center gap-2">
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <IconQrcode className="size-3.5" />
                      {"收款二维码"}
                    </div>
                    {profile.loading ? (
                      <Skeleton className="size-[150px] rounded-md" />
                    ) : fullLink && paypageEnabled ? (
                      <QRCode
                        value={fullLink}
                        size={144}
                        includeMargin
                        frameClassName="p-0.5"
                        downloadable
                        fileName={`${profile.data?.username || "商户"}收款二维码.png`}
                        downloadLabel={"下载收款码"}
                      />
                    ) : (
                      <p className="max-w-[10rem] text-center text-xs text-muted-foreground">
                        {"暂不可用"}
                      </p>
                    )}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Trend Chart（与管理端首页同一张卡片 + 同一开关：settings dashboard_trend_enable 关闭时整块不渲染；
            标题 + 说明 + 右上角汇总，图表高度共用 TrendChart） */}
        {overview?.trend_enable !== false && (
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
        )}
      </div>
    </div>
  )
}

function InfoRow({ label, value, loading }: { label: string; value?: string; loading?: boolean }) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="truncate text-sm font-medium">{loading ? "—" : value || "—"}</div>
    </div>
  )
}
