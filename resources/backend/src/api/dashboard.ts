import apiClient from "@/lib/api-client"
import { APP_MODULE } from "@/lib/config"

export interface TrendPoint {
  date: string
  count: number
  money: number
}

/** 单日收入统计（支付方式/通道/利润三张表共用），对齐老后台 cron 缓存结构 */
export interface DayStat {
  all: number
  profit_all: number
  paytype: Record<string, number>
  channel: Record<string, number>
  profit_paytype: Record<string, number>
}

/** 后台首页统计（全部实时） */
export interface AdminOverview {
  today: { count: number; money: number; profit: number }
  total: { count: number; money: number; merchant_count: number }
  trend: TrendPoint[]
  count1: number
  count2: number
  usermoney: number
  settlemoney: number
  today_deduct_money: number
  today_deduct_count: number
  yesterday_deduct_money: number
  yesterday_deduct_count: number
  success_rate: number
  success_rate_60m: number
  success_rate_30m: number
  success_rate_10m: number
  success_rate_1m: number
  sucnum_60m: number
  sucnum_30m: number
  sucnum_10m: number
  sucnum_1m: number
  paytype: Record<string, string>
  channel: Record<string, string>
  order_today: DayStat
  server_time: string
}

export interface MerchantOverview {
  balance: string
  today: { count: number; money: number }
  yesterday: { count: number; money: number }
  total: { count: number; money: number }
  success_rate: number
  rate: string
  trend: TrendPoint[]
  announcements: { id: number; content: string; created_at: string }[]
}

/** 首页快捷面板配置：金额路由 / 通道限速 */
export interface PayRouteItem {
  enable: number
  min: string
  max: string
  channel: number
}

export interface HomeConfig {
  pay_types: { id: number; code: string; name: string }[]
  channels: { id: number; name: string; payment_type_id: number }[]
  payroute: Record<string, PayRouteItem>
  ratelimit: Record<string, number>
  ratelimit_last: number
}

export interface RatelimitStats {
  show: number
  limit?: number
  allnum?: number
  total?: number
  success?: number
  rate?: number
}

export const dashboardApi = {
  overview: () =>
    APP_MODULE === "merchant"
      ? apiClient.get("/dashboard") as Promise<MerchantOverview>
      : apiClient.get("/dashboard") as Promise<AdminOverview>,
  homeConfig: () =>
    apiClient.get("/dashboard/config") as Promise<HomeConfig>,
  savePayroute: (data: { type_id: number; enable: boolean; min: string; max: string; channel: number }) =>
    apiClient.post("/dashboard/payroute", data) as Promise<null>,
  saveRatelimit: (data: { channel_id: number; value?: number }) =>
    apiClient.post("/dashboard/ratelimit", data) as Promise<null>,
  saveRatelimitLast: (channel_id: number) =>
    apiClient.post("/dashboard/ratelimit/last", { channel_id }) as Promise<null>,
  ratelimitStats: (cid: number) =>
    apiClient.get("/dashboard/ratelimit-stats", { params: { cid } }) as Promise<RatelimitStats>,
}
