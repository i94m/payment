"use client"

import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts"
import type { TrendPoint } from "@/api/dashboard"
import {
  ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig,
} from "@/components/ui/chart"

const trendChartConfig = {
  money: { label: "金额(元)", color: "var(--primary)" },
  count: { label: "笔数", color: "var(--chart-2)" },
} satisfies ChartConfig

/** 趋势图高度：管理端 / 商户端共用（固定 200px，不随容器宽度拉伸变形）。
 *  两侧都用本组件 + 本常量，避免改一处漏一处导致高度不一致。 */
export const TREND_CHART_CLASS = "aspect-auto h-[200px] w-full"

/** 近 N 日收款趋势（金额面积 + 笔数折线），管理端首页 / 商户端首页共用 */
export function TrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <ChartContainer config={trendChartConfig} className={TREND_CHART_CLASS}>
      <AreaChart data={data}>
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
  )
}
