"use client"

import * as React from "react"
import { agentPanelApi } from "@/api"
import type { AgentOverview, AgentProfile } from "@/api"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { CopyableInput } from "@/components/ui/copy-button"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { IconCoin, IconChartBar, IconReceipt, IconWallet, IconLink } from "@tabler/icons-react"

function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function DashboardPage() {
  const [loading, setLoading] = React.useState(true)
  const [profile, setProfile] = React.useState<AgentProfile | null>(null)
  const [overview, setOverview] = React.useState<AgentOverview | null>(null)

  React.useEffect(() => {
    Promise.all([
      agentPanelApi.profile().catch(() => null),
      agentPanelApi.overview().catch(() => null),
    ]).then(([p, o]) => {
      setProfile(p)
      setOverview(o)
      setLoading(false)
    })
  }, [])

  const kpis = [
    { key: "today", label: "今日金额", icon: IconCoin, value: `¥${fmtMoney(overview?.today.money ?? 0)}`, note: "今日收款金额" },
    { key: "yesterday", label: "昨日金额", icon: IconChartBar, value: `¥${fmtMoney(overview?.yesterday.money ?? 0)}`, note: "昨日收款金额" },
    { key: "orders", label: "总订单", icon: IconReceipt, value: `${overview?.total.count ?? 0}`, note: "累计支付订单" },
    { key: "amount", label: "总金额", icon: IconWallet, value: `¥${fmtMoney(overview?.total.money ?? 0)}`, note: "累计收款金额" },
  ]

  const fullLink = profile?.link
    ? (typeof window !== "undefined" ? window.location.origin : "") + profile.link
    : ""

  return (
    <div className="flex flex-1 flex-col">
      <div className="@container/main flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        {/* 业绩四卡 */}
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

        {/* 专属收款链接 + 信息（老系统 index.php 代理链接卡 + link.php 信息表合并） */}
        <div className="grid gap-4 px-4 lg:px-6 lg:grid-cols-2">
          <Card className="shadow-sm">
            <CardHeader>
              <CardDescription className="flex items-center gap-1.5">
                <IconLink className="size-4" />
                {"你的专属收款链接"}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {"将此链接发给付款方，对方点击后输入金额即可付款。通过此链接支付的订单将自动计入你的业绩。"}
              </p>
              <CopyableInput value={fullLink} className="w-full font-mono text-xs" />
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader>
              <CardTitle className="text-base">{"信息"}</CardTitle>
            </CardHeader>
            <CardContent className="text-sm">
              <InfoRow label={"代理名称"} value={profile?.name} loading={loading} />
              <Separator />
              <InfoRow label={"所属商户"} value={profile?.merchant_name} loading={loading} />
              <Separator />
              <InfoRow label={"创建时间"} value={profile?.created_at} loading={loading} />
              <Separator />
              <InfoRow label={"最后登录"} value={profile?.last_login_at} loading={loading} />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function InfoRow({ label, value, loading }: { label: string; value?: string; loading?: boolean }) {
  return (
    <div className="flex items-center justify-between py-2.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{loading ? "—" : value || "—"}</span>
    </div>
  )
}
