"use client"

import * as React from "react"
import { format } from "date-fns"
import { reportApi } from "@/api"
import type { DailyPerformanceData } from "@/api"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { DatePicker } from "@/components/ui/date-picker"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table"
import { ListError } from "@/components/data-list/list-error"
import { toast } from "sonner"
import { IconRefresh, IconSearch } from "@tabler/icons-react"

/** 默认区间：近 30 天（后端同样默认 30 天，最多 90 天） */
const DEFAULT_DAYS = 30

function fmtMoney(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtCount(v: number | string): string {
  return Number(v ?? 0).toLocaleString("zh-CN")
}

function daysAgo(n: number): Date {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d
}

/** 总业绩统计：按天看收款 / 扣量 / 平台利润（数据口径与首页统计点一致） */
export function PerformancePage() {
  const [startDate, setStartDate] = React.useState<Date | undefined>(() => daysAgo(DEFAULT_DAYS - 1))
  const [endDate, setEndDate] = React.useState<Date | undefined>(() => new Date())
  const [data, setData] = React.useState<DailyPerformanceData | null>(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<unknown>(null)

  const load = React.useCallback(async (start?: Date, end?: Date) => {
    if (!start || !end) return
    setLoading(true)
    setError(null)
    try {
      setData(await reportApi.daily({
        start_date: format(start, "yyyy-MM-dd"),
        end_date: format(end, "yyyy-MM-dd"),
      }))
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [])

  React.useEffect(() => {
    void load(startDate, endDate)
    // 首屏只拉一次；之后的查询由「查询」按钮或日期变化触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const apply = () => {
    if (!startDate || !endDate) {
      toast.warning("请选择开始日期与结束日期")
      return
    }
    if (endDate < startDate) {
      toast.warning("结束日期不能早于开始日期")
      return
    }
    void load(startDate, endDate)
  }

  const reset = () => {
    const start = daysAgo(DEFAULT_DAYS - 1)
    const end = new Date()
    setStartDate(start)
    setEndDate(end)
    void load(start, end)
  }

  const items = data?.items ?? []
  // 表格按日期倒序（最新在上）
  const rows = React.useMemo(() => [...items].reverse(), [items])

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <div className="px-4 lg:px-6">
          <h2 className="text-lg font-semibold">{"总业绩统计"}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {"按天汇总收款、扣量与平台利润（收款按支付时间归日，扣量按订单创建时间归日）。"}
          </p>
        </div>

        <Card className="mx-4 lg:mx-6">
          <CardContent className="flex flex-wrap items-end gap-2 pt-4">
            <DatePicker className="h-9 w-[160px]" placeholder={"开始日期"} value={startDate} onChange={setStartDate} />
            <span className="pb-2 text-sm text-muted-foreground">{"至"}</span>
            <DatePicker className="h-9 w-[160px]" placeholder={"结束日期"} value={endDate} onChange={setEndDate} />
            <Button size="sm" onClick={apply}>
              <IconSearch className="size-4" />
              {"查询"}
            </Button>
            <Button variant="outline" size="sm" onClick={reset}>
              <IconRefresh className="size-4" />
              {"重置"}
            </Button>
            <span className="pb-2 text-xs text-muted-foreground">{"最多查询 90 天，默认近 30 天"}</span>
          </CardContent>
        </Card>

        {error ? (
          <div className="px-4 lg:px-6">
            <ListError error={error} onRetry={apply} title="业绩数据加载失败" />
          </div>
        ) : (
          <>
            {/* 按天明细 */}
            <Card className="mx-4 gap-3 py-4 lg:mx-6">
              <CardHeader>
                <CardTitle>{"每日明细"}</CardTitle>
                <CardDescription>
                  {data ? `${data.start_date} ~ ${data.end_date}（共 ${items.length} 天）` : ""}
                </CardDescription>
                <CardAction>
                  <Button variant="outline" size="icon" className="size-7" onClick={apply}
                    title={"刷新"} aria-label="刷新">
                    <IconRefresh className="size-3.5" />
                  </Button>
                </CardAction>
              </CardHeader>
              <CardContent className="px-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-4">{"日期"}</TableHead>
                      <TableHead className="text-right">{"收款笔数"}</TableHead>
                      <TableHead className="text-right">{"收款金额"}</TableHead>
                      <TableHead className="text-right">{"扣量笔数"}</TableHead>
                      <TableHead className="text-right">{"扣量金额"}</TableHead>
                      <TableHead className="pr-4 text-right">{"平台利润"}</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {loading ? (
                      <TableRow>
                        <TableCell colSpan={6} className="py-6">
                          <Skeleton className="h-6 w-full" />
                        </TableCell>
                      </TableRow>
                    ) : rows.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={6} className="py-6 text-center text-sm text-muted-foreground">
                          {"没有数据"}
                        </TableCell>
                      </TableRow>
                    ) : rows.map((it) => (
                      <TableRow key={it.date}>
                        <TableCell className="pl-4 text-sm tabular-nums">{it.date}</TableCell>
                        <TableCell className="text-right text-sm tabular-nums">{fmtCount(it.paid_count)}</TableCell>
                        <TableCell className="text-right text-sm tabular-nums">¥{fmtMoney(it.paid_money)}</TableCell>
                        <TableCell className="text-right text-sm tabular-nums">{fmtCount(it.deduct_count)}</TableCell>
                        <TableCell className="text-right text-sm tabular-nums">¥{fmtMoney(it.deduct_money)}</TableCell>
                        <TableCell className="pr-4 text-right text-sm tabular-nums">¥{fmtMoney(it.profit)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}
