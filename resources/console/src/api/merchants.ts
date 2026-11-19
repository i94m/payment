import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface MerchantData {
  id: number
  pid: number
  username: string
  public_key: string
  api_secret_masked: string    // MD5 签名密钥（掩码，如 abcd****wxyz）
  has_api_secret: boolean
  rsa2_enable: boolean         // RSA2 是否对商户开放（默认关闭）
  balance: string
  frozen_balance: string
  status: number
  rate: string
  default_notify_url: string
  default_return_url: string
  email: string
  phone: string
  qq: string
  url: string
  allowed_domains: string
  pay_status: number          // 0=关闭 1=正常 2=待审核
  settle_status: number       // 0=关闭 1=正常
  random_enable: boolean
  random_min: string
  random_max: string
  random_integer: boolean     // 仅整数金额（关闭时支持两位小数）
  random_ratelimit: number
  deduct_enable: boolean        // 扣量开关（商户级唯一配置源）
  deduct_frequency: number      // 每 N 笔扣 1
  deduct_minmoney: string       // 起扣金额（单笔 ≥ 该值才扣，0=不限）
  fixed: boolean
  settle_type: number           // 1=支付宝 2=微信 3=QQ 4=银行卡
  settle_account: string
  settle_name: string
  auto_settle: boolean
  created_at: string
  last_login_at: string
}

export interface MerchantListParams {
  page?: number
  per_page?: number
  keyword?: string
  /** 精细搜索字段（column 参数） */
  search_field?: string
  /** 综合状态筛选（dstatus 参数） */
  dstatus?: string
  /** 高级搜索：超过 N 天无订单 */
  order_days?: number | ""
  /** 排序：money_desc/money_asc/lasttime_desc/lasttime_asc */
  order?: string
}

export interface MerchantRecordData {
  id: number
  biz_type: string
  amount: string
  balance_before: string
  balance_after: string
  order_trade_no: string
  remark: string
  created_at: string
}

export interface AdminRecordData extends MerchantRecordData {
  merchant_id: number
  merchant_pid: number | string
  merchant_username: string
  biz_type_name: string
}

export interface AdminRecordListParams {
  page?: number
  per_page?: number
  /** 商户 ID（商户列表「资金明细」快捷跳转默认带上） */
  merchant_id?: string | number
  /** 操作类型（biz_type） */
  biz_type?: string
  /** 精细搜索字段：biz_type/amount/order_trade_no */
  search_field?: string
  keyword?: string
  /** 时间范围 "start,end" YYYY-MM-DD */
  addtime?: string
}

export const merchantsApi = {
  list: (params: MerchantListParams & Record<string, unknown>) =>
    apiClient.post("/merchant/list", params) as Promise<PaginatedResponse<MerchantData>>,

  get: (id: number) => apiClient.get(`/merchant/${id}`) as Promise<MerchantData>,

  create: (data: {
    username: string; password: string; rate?: string
    email?: string; phone?: string; qq?: string; url?: string
    status?: number; pay_status?: number; settle_status?: number
  }) =>
    apiClient.post("/merchant", data) as Promise<MerchantData>,

  update: (id: number, data: Record<string, unknown>) =>
    apiClient.put(`/merchant/${id}`, data) as Promise<MerchantData>,

  remove: (id: number) =>
    apiClient.delete(`/merchant/${id}`) as Promise<null>,

  /** 生成 RSA 密钥对（公钥落库，私钥仅返回一次；RSA2 签名需商户开启） */
  generateRsaKey: (id: number) =>
    apiClient.post(`/merchant/${id}/rsakey`) as Promise<{ public_key: string; private_key: string }>,

  /** 重置商户 MD5 签名密钥（旧密钥立即失效） */
  resetApiSecret: (id: number) =>
    apiClient.post(`/merchant/${id}/apisecret`) as Promise<{ api_secret: string }>,

  /** 平台RSA公钥（由 platform_rsa_private_key 派生，商户用于验证平台通知签名） */
  platformKey: () =>
    apiClient.get("/merchant/platform-key") as Promise<{ public_key: string }>,

  adjustBalance: (id: number, data: { amount: string; remark?: string }) =>
    apiClient.post(`/merchant/${id}/balance`, data) as Promise<{ balance: string; record_id: number }>,

  /** setUser 式状态切换：type ∈ user/pay/settle/fixed/rsa2 */
  setStatus: (id: number, type: "user" | "pay" | "settle" | "fixed" | "rsa2", status: number) =>
    apiClient.post(`/merchant/${id}/status`, { type, status }) as Promise<null>,

  /** 随机金额（min==max 固定金额；random_integer=仅整数金额） */
  setRandom: (id: number, data: { random_enable: boolean; random_min?: string; random_max?: string; random_integer?: boolean }) =>
    apiClient.post(`/merchant/${id}/random`, data) as Promise<null>,

  /** 批量随机金额（跳过 配置锁定 商户） */
  setAllRandom: (data: { random_enable: boolean; random_min?: string; random_max?: string; random_integer?: boolean }) =>
    apiClient.post("/merchant/random/set-all", data) as Promise<string>,

  /** 限制拉单：同一付款人 IP 每分钟建单上限，0=不限 */
  setRatelimit: (id: number, data: { random_ratelimit: number }) =>
    apiClient.post(`/merchant/${id}/ratelimit`, data) as Promise<null>,

  /** 扣量（商户级）：开关 + 频次 + 起扣金额；开启时频次必填 */
  setDeduct: (id: number, data: { deduct_enable: boolean; deduct_frequency?: number; deduct_minmoney?: string }) =>
    apiClient.post(`/merchant/${id}/deduct`, data) as Promise<null>,

  /** 批量扣量：开启时下发频次与起扣金额（金额留空 = 不限）；关闭只写开关位 */
  setAllDeduct: (data: { deduct_enable: boolean; deduct_frequency?: number; deduct_minmoney?: string }) =>
    apiClient.post("/merchant/deduct/set-all", data) as Promise<string>,

  /** SSO 一键登录商户后台，返回一次性落地 URL */
  sso: (id: number) =>
    apiClient.post(`/merchant/${id}/sso`) as Promise<{ url: string }>,

  /** 导出（与列表同过滤条件） */
  exportUrl: "/merchant/export",
}

// 重置登录密码（生成随机密码返回一次）
export const merchantSecurityApi = {
  /** 重置登录密码：不传密码时后端生成随机密码并返回一次 */
  resetPassword: (id: number, password = "") =>
    apiClient.post(`/merchant/${id}/resetpwd`, { password }) as Promise<{ password: string }>,
}

// 资金明细（全商户流水，商户列表「资金明细」快捷跳转的落地页）
export const adminRecordsApi = {
  list: (params: AdminRecordListParams & Record<string, unknown>) =>
    apiClient.post("/record/list", params) as Promise<PaginatedResponse<AdminRecordData>>,

  /** 导出（与列表同过滤条件） */
  exportUrl: "/record/export",
}
