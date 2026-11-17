import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

// ============ 插件 ============

export interface PluginInput {
  name: string
  label: string
  type?: string
  required?: boolean
  placeholder?: string
  [key: string]: unknown
}

export interface PluginData {
  name: string
  showname: string
  supported_types: string[]
  inputs: PluginInput[]
  is_enabled: boolean
  version: string
  /** 插件来源：builtin=镜像内置，third=三方（storage/plugins），unknown=已失效 */
  source: string
  /** 盘上已加载但还没同步进插件表（点「已安装插件」刷新即落库） */
  pending?: boolean
}

/** 同步插件目录的返回（POST /pay/plugins/sync） */
export interface PluginSyncResult {
  synced: number
  removed: string[]
  referenced: string[]
  message: string
}

// ============ 支付方式 ============

export interface PaymentTypeData {
  id: number
  /** 调用值（开放 API type 参数，仅限英文数字） */
  code: string
  /** 显示名称 */
  name: string
  /** 支持设备：0=PC+Mobile 1=PC 2=Mobile */
  device: number
  status: number
  /** 图标 URL（可选，未配置时前端不渲染，避免拼不存在的静态路径） */
  icon?: string
}

export interface PaymentTypePayload {
  code: string
  name: string
  device: number
}

export interface PaymentTypeStats {
  today_count?: number
  today_amount?: string
  yesterday_count?: number
  yesterday_amount?: string
}

// ============ 通道 ============

export interface ChannelData {
  id: number
  name: string
  plugin_name: string
  payment_type_id: number
  merchant_rate: string
  cost_rate: string
  status: number
  mode: number
  weight: number
  min_amount: string
  max_amount: string
  day_limit: string
  day_limit_count: number
  sort: number
  /** 调试日志开关：0=关 1=开 */
  debug_log: number
  config: Record<string, string> // 脱敏后的凭证
}

export interface ChannelDetailData extends ChannelData {
  inputs: PluginInput[] // 凭证渲染 schema（仅详情返回）
  /**
   * 本次详情是否回显了明文凭证：`reveal=1` 且账号具备 channel.update 时才为 true。
   * 无权限时后端静默降级为脱敏凭证（不报 403），前端据此提示而不是当成功。
   */
  config_revealed?: boolean
}

export interface ChannelStats {
  today_count?: number
  today_amount?: string
  yesterday_count?: number
  yesterday_amount?: string
}

export interface ChannelTestPayResult {
  trade_no: string
  cashier_url: string
  pay_type: string
  pay_info: string
  debug?: Record<string, unknown>
}

// 测试失败时 err() errors[0] 携带的调试信息
export interface ChannelTestDebug {
  time: string
  client_ip: string
  channel: Record<string, unknown>
  order: Record<string, unknown>
  upstream_request: Record<string, unknown> | null
  error: string
}

export interface ChannelPayload {
  name: string
  plugin_name: string
  payment_type_id: number
  merchant_rate: string
  cost_rate: string
  status: number
  mode: number
  weight: number
  min_amount: string
  max_amount: string
  day_limit: string
  day_limit_count: number
  sort: number
  /** 调试日志开关：0=关 1=开（缺省=不触碰，toggleStatus 透传用） */
  debug_log?: number
  config: Record<string, string>
}

export const payApi = {
  plugins: () => apiClient.get("/pay/plugins") as Promise<PluginData[]>,

  // 扫描 plugins/ 目录刷新插件注册表（支付通道页「刷新插件」按钮；其他 worker 刷新页面即为最新）
  syncPlugins: () => apiClient.post("/pay/plugins/sync") as Promise<PluginSyncResult>,

  // 上传安装编译后的插件包（zip：build_plugin.sh 产物目录，仅含 .so）
  uploadPlugin: (file: File) => {
    const fd = new FormData()
    fd.append("file", file)
    return apiClient.post("/pay/plugins/upload", fd) as Promise<Record<string, string>>
  },

  payTypes: () => apiClient.get("/pay/types") as Promise<PaymentTypeData[]>,

  // 全部支付方式今日/昨日收款统计（键为支付方式 id 字符串）
  typeStats: () => apiClient.get("/pay/types/stats") as Promise<Record<string, PaymentTypeStats>>,

  createPayType: (data: PaymentTypePayload) =>
    apiClient.post("/pay/types", data) as Promise<{ id: number }>,

  updatePayType: (id: number, data: PaymentTypePayload) =>
    apiClient.put(`/pay/types/${id}`, data),

  // 列表开关直接切换状态
  setPayTypeStatus: (id: number, status: number) =>
    apiClient.put(`/pay/types/${id}/status`, { status }),

  deletePayType: (id: number) => apiClient.delete(`/pay/types/${id}`),

  channels: (params?: { page?: number; per_page?: number; payment_type_id?: number; plugin_name?: string }) =>
    apiClient.get("/pay/channels", { params }) as Promise<PaginatedResponse<ChannelData>>,

  channelDetail: (id: number, opts?: { reveal?: boolean }) =>
    apiClient.get(`/pay/channels/${id}`, { params: opts?.reveal ? { reveal: 1 } : undefined }) as Promise<ChannelDetailData>,

  createChannel: (data: ChannelPayload) => apiClient.post("/pay/channels", data) as Promise<ChannelData>,

  updateChannel: (id: number, data: ChannelPayload) =>
    apiClient.put(`/pay/channels/${id}`, data) as Promise<ChannelData>,

  deleteChannel: (id: number) => apiClient.delete(`/pay/channels/${id}`),

  // 全部通道今日/昨日收款统计（键为通道 id 字符串）
  channelStats: () => apiClient.get("/pay/channels/stats") as Promise<Record<string, ChannelStats>>,

  testChannelPay: (id: number, data: { name: string; amount: string }) =>
    apiClient.post(`/pay/channels/${id}/testpay`, data) as Promise<ChannelTestPayResult>,
}
