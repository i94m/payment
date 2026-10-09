import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "."

export interface AgentData {
  id: number
  name: string
  status: number
  remark: string
  link: string
  created_at: string
  last_login_at: string
}

/** 代理业绩明细行（按代理分解） */
export interface AgentPerformanceRow {
  id: number
  name: string
  today_money: string
  yesterday_money: string
  total_money: string
  total_count: number
}

/** 代理业绩明细 summary：商户整体口径（含无代理订单） */
export interface AgentPerformanceSummary {
  today_money: string
  yesterday_money: string
  total_money: string
  total_count: number
}

export interface AgentListParams {
  page?: number
  per_page?: number
  kw?: string
}

export const agentsApi = {
  list: (params: AgentListParams = {}) =>
    apiClient.get("/agent/list", { params }) as Promise<PaginatedResponse<AgentData>>,

  create: (payload: { name: string; password: string; remark?: string }) =>
    apiClient.post("/agent", payload) as Promise<AgentData>,

  update: (id: number, payload: { name?: string; password?: string; remark?: string; status?: number }) =>
    apiClient.put(`/agent/${id}`, payload) as Promise<AgentData>,

  remove: (id: number) => apiClient.delete(`/agent/${id}`),

  performanceList: (params: AgentListParams = {}) =>
    apiClient.get("/agent/performance/list", { params }) as Promise<PaginatedResponse<AgentPerformanceRow>>,

  /** 商户 SSO 代登录代理面板：返回一次性落地 URL（/agent/#/sso-login?token=...） */
  sso: (id: number) => apiClient.post(`/agent/${id}/sso`) as Promise<{ url: string }>,
}
