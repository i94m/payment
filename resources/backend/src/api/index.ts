// epay 后端统一包络 {code, message, data} 已在 api-client 拦截器解包：
// 成功 → 直接拿到 data；失败 → reject {code, message}。

// Laravel 风格分页结构（app/responses.paginate）
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  total_page: number
  count: number
  per_page: number
  current_page: number
  has_more_pages: boolean
  has_pages: boolean
  /** 部分列表接口附加的汇总（如订单列表 summary.money） */
  summary?: Record<string, unknown>
  /** 模板统计卡片（epay 后端暂无，保留可选兼容 data-list） */
  statistics?: StatisticItem[]
}

// data-list 统计卡片结构
export interface StatisticItem {
  label: string
  value: string | number
  unit?: string
  color?: string
}

export { authApi } from "./auth"
export { dashboardApi } from "./dashboard"
export { merchantsApi } from "./merchants"
export type { MerchantData, MerchantListParams } from "./merchants"
export { merchantSecurityApi } from "./merchants"
export { adminRecordsApi } from "./merchants"
export type { AdminRecordData, AdminRecordListParams } from "./merchants"
export { ordersApi } from "./orders"
export type { OrderData, OrderListParams } from "./orders"
export { notifyLogsApi } from "./notifylogs"
export type { NotifyLogData } from "./notifylogs"
export { payApi } from "./pay"
export type { PluginData, PaymentTypeData, PaymentTypePayload, PaymentTypeStats, ChannelData, ChannelDetailData, ChannelStats, ChannelTestDebug, PluginInput } from "./pay"
export { settleApi } from "./settle"
export type { AdminSettleData, MerchantSettleData, SettleInfo } from "./settle"
export { recordsApi } from "./records"
export type { MerchantRecordData, MerchantSettlementData } from "./records"
export { permissionApi } from "./permission"
export type { AdminAccountData, PermissionNode } from "./permission"
export { accountApi } from "./account"
export type { ApiKeyData, SettingItem } from "./account"
export { securityApi } from "./account"
export { agentsApi } from "./agents"
export type { AgentData, AgentListParams, AgentPerformanceRow, AgentPerformanceSummary } from "./agents"
export { agentPanelApi } from "./agent"
export type { AgentProfile, AgentOverview, AgentStat, AgentOrderData } from "./agent"
export { messagesApi } from "./messages"
export type { MessageData } from "./messages"
export { paypageApi } from "./paypage"
export type { PaypageSettings } from "./paypage"
export { announcesApi } from "./announces"
export type { AnnounceData } from "./announces"
export type { SettleInfoExtended } from "./settle"
