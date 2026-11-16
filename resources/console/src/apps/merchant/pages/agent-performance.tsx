"use client"

import * as React from "react"
import { agentsApi } from "@/api"
import type { AgentPerformanceRow } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { DataTable, DataTablePagination } from "@/components/data-table/"
import { FilterCard } from "@/components/filter-card"
import { useDataList, ListError } from "@/components/data-list/"
import { IconSearch, IconRefresh, IconCoin, IconChartBar, IconReceipt, IconWallet } from "@tabler/icons-react"

function fmtMoney(v: unknown): string {
  return Number(v ?? 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function AgentPerformancePage() {
  const {
    table, loading, error, keyword, setKeyword, applyFilters, refresh, summary,
  } = useDataList<AgentPerformanceRow>({
    // keyword 搜索框映射为后端的 kw（代理名称模糊）
    fetchFn: (params) => {
      const { keyword: kw, ...rest } = params as { keyword?: string }
      return agentsApi.performanceList({ ...rest, kw: kw || "" })
    },
    columns: [
      {
        accessorKey: "name",
        enableSorting: false,
        header: () => "代理名称",
        cell: ({ row }) => <span className="text-sm font-medium">{row.original.name}</span>,
        meta: { headClassName: "min-w-[120px]" },
      },
      {
        accessorKey: "today_money",
        enableSorting: false,
        header: () => "今日金额",
        cell: ({ row }) => <span className="text-sm tabular-nums">¥{row.original.today_money}</span>,
        meta: { headClassName: "w-[110px]" },
      },
      {
        accessorKey: "yesterday_money",
        enableSorting: false,
        header: () => "昨日金额",
        cell: ({ row }) => <span className="text-sm tabular-nums">¥{row.original.yesterday_money}</span>,
        meta: { headClassName: "w-[110px]" },
      },
      {
        accessorKey: "total_money",
        enableSorting: false,
        header: () => "总金额",
        cell: ({ row }) => (
          <span className="text-sm font-medium tabular-nums">¥{row.original.total_money}</span>
        ),
        meta: { headClassName: "w-[110px]" },
      },
      {
        accessorKey: "total_count",
        enableSorting: false,
        header: () => "总订单",
        cell: ({ row }) => <span className="text-sm tabular-nums">{row.original.total_count}</span>,
        meta: { headClassName: "w-[90px]" },
      },
    ],
    perPage: 15,
  })

  // 商户整体口径汇总（含无代理订单），随当前筛选外的全量数据返回
  const kpis = [
    { key: "today", label: "今日金额", icon: IconCoin, value: `¥${fmtMoney(summary?.today_money)}` },
    { key: "yesterday", label: "昨日金额", icon: IconChartBar, value: `¥${fmtMoney(summary?.yesterday_money)}` },
    { key: "totalMoney", label: "总金额", icon: IconWallet, value: `¥${fmtMoney(summary?.total_money)}` },
    { key: "totalCount", label: "总订单", icon: IconReceipt, value: `${Number(summary?.total_count ?? 0)} 笔` },
  ]

  const handleReset = () => {
    setKeyword("")
    applyFilters()
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="@container/main flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        {/* 汇总四卡（对齐老系统 agent_performance.php 顶部统计） */}
        <div className="grid grid-cols-1 gap-4 px-4 lg:px-6 @xl/main:grid-cols-2 @5xl/main:grid-cols-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card">
          {kpis.map((kpi) => (
            <Card key={kpi.key} className="@container/card shadow-sm">
              <CardHeader>
                <CardDescription className="flex items-center gap-1.5">
                  <kpi.icon className="size-4" />
                  {kpi.label}
                </CardDescription>
                <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
                  {loading && !summary ? "—" : kpi.value}
                </CardTitle>
              </CardHeader>
            </Card>
          ))}
        </div>

        <FilterCard>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 items-end">
            <Input
              placeholder={"搜索代理名称"}
              className="h-9"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") applyFilters() }}
            />
          </div>

          <div className="flex items-center gap-2">
            <Button size="sm" onClick={() => applyFilters()}>
              <IconSearch className="size-4" />
              {"查询"}
            </Button>
            <Button variant="outline" size="sm" onClick={handleReset}>
              <IconRefresh className="size-4" />
              {"重置"}
            </Button>
          </div>
        </FilterCard>

        <div className="px-4 lg:px-6">
          {error ? (
            <ListError error={error} onRetry={refresh} />
          ) : (
            <DataTable table={table} loading={loading} emptyMessage={"暂无数据"}>
              <DataTablePagination table={table} />
            </DataTable>
          )}
        </div>
      </div>
    </div>
  )
}
