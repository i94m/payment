// 代理面板（agent 模块）自身 API：BASE_URL=/agent-api，与商户端 agents.ts 区分
import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "."

export interface AgentProfile {
  id: number
  name: string
  username: string
  remark: string
  status: number
  created_at: string
  last_login_at: string
  merchant_name: string
  merchant_pid: number
  link: string
}

export interface AgentStat {
  count: number
  money: string
}

export interface AgentOverview {
  today: AgentStat
  yesterday: AgentStat
  total: AgentStat
}

export interface AgentOrderData {
  trade_no: string
  money: string
  realmoney: string
  subject: string
  created_at: string
  paid_at: string
  status: number
}

export const agentPanelApi = {
  profile: () => apiClient.get("/auth/profile") as Promise<AgentProfile>,

  overview: () => apiClient.get("/dashboard") as Promise<AgentOverview>,

  orders: (params: {
    page?: number
    per_page?: number
    starttime?: string
    endtime?: string
    status?: number
  }) => apiClient.get("/order/list", { params }) as Promise<PaginatedResponse<AgentOrderData>>,
}
