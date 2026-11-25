import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

// ============ 插件 ============

export interface PluginInput {
  name: string
  /** 展示名（后端把插件的 title 归一化为 label；缺省时退回 name） */
  label?: string
  /** 控件类型：input（默认）/ textarea（长凭证，如公钥私钥）/ select（配 options） */
  type?: string
  required?: boolean
  placeholder?: string
  /** select 型的选项：{value: label} 或字符串数组 */
  options?: Record<string, unknown> | string[]
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
  /** 盘上已加载但还没同步进插件表（点「刷新插件」即落库） */
  pending?: boolean
}

/** 同步插件目录的返回（POST /pay/plugins/sync） */
export interface PluginSyncResult {
  synced: number
  /** 本次新增入库的插件数（0 表示盘上没有新插件） */
  added: number
  removed: string[]
  referenced: string[]
  /** 加载失败的插件目录 → 原因（导入报错 / 没有 BasePayment 子类等） */
  errors?: Record<string, string>
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
  /** 图标地址：内置图标库路径（/icons/pay/*.svg）或自定义 URL，留空不显示 */
  icon?: string
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
  /** 默认通道：全平台唯一，商户端对接信息展示它的名称（不参与分流） */
  is_default: boolean
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
  /** 本次测试落库的测试单号（order_type=2，可在订单页按「测试单」筛到） */
  trade_no: string
  /** 插件返回的支付动作：jump / qrcode / scheme / html */
  pay_type: string
  /** 支付链接、二维码地址或 HTML 表单（按 pay_type 解释） */
  pay_info: string
  debug?: Record<string, unknown>
}

// 测试失败时 err() errors[0] 携带的调试信息
export interface ChannelTestDebug {
  time: string
  client_ip: string
  channel: Record<string, unknown>
  /** 本次测试单的下单参数（已落库的测试单） */
  request: Record<string, unknown>
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

// 通道编码弹窗的通道候选：挑通道要用的字段 + 商户费率（编码模块要展示），不含成本/凭证
export interface ChannelOptionData {
  id: number
  name: string
  plugin_name: string
  payment_type_id: number
  status: number
  /** 默认通道（全平台唯一） */
  is_default: boolean
  /** 通道商户费率（%），编码模块展示用 */
  merchant_rate: string
}

// ============ 通道编码 ============

export interface ChannelCodeItemData {
  channel_id: number
  weight: number
  sort: number
  channel_name: string
  plugin_name: string
  payment_type_id: number
  /** 通道自身的全局状态（0=全局停用，绑在编码里也分流不到） */
  channel_status: number
  /** 通道商户费率（%），编码模块展示用 */
  merchant_rate: string
}

export interface ChannelCodeData {
  id: number
  code: string
  name: string
  remark: string
  /** 0=随机 2=权重（轮询已下线） */
  mode: number
  status: number
  /** 编码级单笔金额区间（0=不限；min/max 都 0 = 不限制） */
  min_amount: string
  max_amount: string
  /** 限额展示文案：`1.00 ~ 9.00` / `≥ 5.00` / `≤ 100.00` / `不限` */
  amount_text: string
  item_count: number
  members: ChannelCodeItemData[]
  today_count?: number
  today_amount?: string
  yesterday_count?: number
  yesterday_amount?: string
  created_at: string
}

export interface ChannelCodeDetailData {
  id: number
  code: string
  name: string
  remark: string
  mode: number
  status: number
  min_amount: string
  max_amount: string
  amount_text: string
  items: ChannelCodeItemData[]
}

export interface ChannelCodePayload {
  code: string
  name: string
  remark: string
  mode: number
  /** 不传 = 保持原状态（表单已去掉启用开关，编辑不会把停用编码改回启用）；创建默认启用 */
  status?: number
  /** 单笔金额区间（0=不限，两端都 0=不限制） */
  min_amount: string
  max_amount: string
  items: { channel_id: number; weight: number; sort?: number }[]
}

export const payApi = {
  plugins: () => apiClient.get("/pay/plugins") as Promise<PluginData[]>,

  // 扫描 plugins/ 目录刷新插件注册表（支付通道页「刷新插件」按钮；其他 worker 刷新页面即为最新）
  syncPlugins: () => apiClient.post("/pay/plugins/sync") as Promise<PluginSyncResult>,

  // 上传安装编译后的插件包（zip：build_plugin.sh 产物目录，仅含 .so）
  uploadPlugin: (file: File) => {
    const fd = new FormData()
    fd.append("file", file)
    // 必须显式声明 multipart：apiClient 默认 Content-Type=application/json，
    // axios 会把 FormData 序列化成 JSON（文件变成空对象），后端只会回
    // 422 body.file Field required。声明成 multipart 后 axios 交给浏览器写 boundary。
    return apiClient.post("/pay/plugins/upload", fd, {
      headers: { "Content-Type": "multipart/form-data" },
    }) as Promise<Record<string, string>>
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

  /** 通道列表「调试日志」开关：只改 debug_log，不触碰费率/限额等字段 */
  setChannelDebugLog: (id: number, enabled: boolean) =>
    apiClient.put(`/pay/channels/${id}/debug-log`, { enabled }),

  /** 设为默认通道（全平台唯一）：只改 is_default，不触碰费率/限额/凭证 */
  setChannelDefault: (id: number, enabled = true) =>
    apiClient.put(`/pay/channels/${id}/default`, { enabled }) as Promise<{ id: number; is_default: boolean }>,

  // 全部通道今日/昨日收款统计（键为通道 id 字符串）
  channelStats: () => apiClient.get("/pay/channels/stats") as Promise<Record<string, ChannelStats>>,

  testChannelPay: (id: number, data: { name: string; amount: string }) =>
    apiClient.post(`/pay/channels/${id}/testpay`, data) as Promise<ChannelTestPayResult>,

  // ============ 下游通道编码 ============

  /** 编码弹窗的通道候选：channel_code.view 即可读，不需要 channel.view */
  channelOptions: () => apiClient.get("/pay/channel-options") as Promise<ChannelOptionData[]>,

  channelCodes: (params?: { page?: number; per_page?: number; keyword?: string; status?: number }) =>
    apiClient.get("/pay/channel-codes", { params }) as Promise<PaginatedResponse<ChannelCodeData>>,

  channelCodeDetail: (id: number) =>
    apiClient.get(`/pay/channel-codes/${id}`) as Promise<ChannelCodeDetailData>,

  createChannelCode: (data: ChannelCodePayload) =>
    apiClient.post("/pay/channel-codes", data) as Promise<{ id: number; code: string }>,

  updateChannelCode: (id: number, data: ChannelCodePayload) =>
    apiClient.put(`/pay/channel-codes/${id}`, data) as Promise<{ id: number; code: string }>,

  deleteChannelCode: (id: number) => apiClient.delete(`/pay/channel-codes/${id}`),

  /** 编码总闸：只改开关，不触碰成员与权重 */
  setChannelCodeStatus: (id: number, status: number) =>
    apiClient.put(`/pay/channel-codes/${id}/status`, { status }) as Promise<{ id: number; status: number }>,
}
