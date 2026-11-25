import apiClient from "@/lib/api-client"

/** 单日业绩：收款 / 扣量 / 平台利润（口径与首页统计一致） */
export interface DailyPerformanceItem {
  date: string
  paid_count: number
  paid_money: number
  deduct_count: number
  deduct_money: number
  profit: number
}

export interface DailyPerformanceTotal {
  paid_count: number
  paid_money: number
  deduct_count: number
  deduct_money: number
  profit: number
}

export interface DailyPerformanceData {
  start_date: string
  end_date: string
  items: DailyPerformanceItem[]
  total: DailyPerformanceTotal
}

export const reportApi = {
  /** 总业绩统计：按天汇总（默认近 30 天，最多 90 天） */
  daily: (params: { start_date?: string; end_date?: string }) =>
    apiClient.get("/report/daily", { params }) as Promise<DailyPerformanceData>,
}
