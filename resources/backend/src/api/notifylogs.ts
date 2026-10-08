import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface NotifyLogData {
  id: number
  kind: string // merchant=通知商户 upstream=上游回调
  trade_no: string
  url: string
  request: string | null
  response: string | null
  http_status: number | null
  attempt: number
  success: boolean
  created_at: string
}

export interface NotifyLogListParams {
  page?: number
  per_page?: number
  trade_no?: string
  kind?: string
  success?: string | number | ""
}

export const notifyLogsApi = {
  /** 过滤条件走 body，分页走 query（后端如此约定） */
  list: (params: NotifyLogListParams) => {
    const { page, per_page, ...filters } = params
    return apiClient.post("/notifylog/list", filters, {
      params: { page, per_page },
    }) as Promise<PaginatedResponse<NotifyLogData>>
  },
}
