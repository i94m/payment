import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

/**
 * 操作审计（只读）。审计行由后端在敏感操作成功后写入，
 * 前端只做查询与前后值展示，没有任何写接口。
 */
export interface AuditLogData {
  id: number
  /** 操作人身份域：admin=后台账号 / merchant / agent / system=定时任务 */
  actor_guard: string
  actor_id: number
  actor_name: string
  /** 点分动作名，如 merchant.balance_adjust / settle.pay / plugin.install */
  action: string
  target_type: string
  target_id: string
  /** 变更前快照（敏感字段已由后端脱敏为 ******） */
  before: Record<string, unknown> | null
  /** 变更后快照 */
  after: Record<string, unknown> | null
  ip: string
  user_agent: string
  remark: string
  created_at: string
}

export interface AuditListParams {
  page?: number
  per_page?: number
  /** 动作名模糊匹配（后端 contains） */
  action?: string
  target_type?: string
  target_id?: string
  actor_id?: string
  /** 操作人身份域：admin / merchant / agent / system */
  guard?: string
  /** 开始时间，支持 YYYY-MM-DD 或 YYYY-MM-DD HH:mm:ss */
  start?: string
  /** 结束时间，纯日期时后端补到当天 23:59:59 */
  end?: string
}

export const auditApi = {
  list: (params?: AuditListParams) =>
    apiClient.get("/audit/list", { params }) as Promise<PaginatedResponse<AuditLogData>>,

  /** 已出现过的动作名（筛选下拉数据源） */
  actions: () => apiClient.get("/audit/actions") as Promise<string[]>,
}
