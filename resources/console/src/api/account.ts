import apiClient from "@/lib/api-client"

export interface ApiKeyData {
  pid: number
  username: string // 商户名称（登录用户名）
  api_secret: string // MD5 签名密钥（默认算法，32 位小写 hex）
  public_key: string // 商户 RSA 公钥（未配置为空串）
  rsa2_enable: boolean // 是否启用 RSA2（默认关闭；启用后 MD5 失效）
  platform_public_key: string // 平台公钥（由平台私钥派生，未配置为空串）
  // 对接信息展示的默认通道（全平台唯一、启用中），没有则为 null（整行不展示）；
  // codes = 绑定该通道的启用编码，展示的是编码（下游可直接当 channel_code 传）
  default_channel: { name: string; payment_type: string; codes: string[] } | null
}

/** 商户端「支付通道」卡片：一条启用中的通道编码及其可用支付方式 */
export interface MerchantChannelCode {
  code: string
  name: string
  remark: string
  /** 编码级单笔金额区间（0=不限；两端都 0=不限制） */
  min_amount: string
  max_amount: string
  /** 限额展示文案：`1.00 ~ 9.00` / `≥ 5.00` / `≤ 100.00` / `不限` */
  amount_text: string
  /** 该商户实际生效的费率（%）：区间两端相同即为单一费率；空=未知 */
  fee_rate_min: string
  fee_rate_max: string
  /** 费率来源：merchant=我的专属费率，channel=按通道费率，none=未知 */
  fee_rate_source: "merchant" | "channel" | "none"
  // 该编码内启用中通道支持的支付方式（停用通道不计入；空数组=暂不可用）
  pay_types: { code: string; name: string; icon: string }[]
}

export interface SignDebugResult {
  sign_content: string
  active_sign_type: "MD5" | "RSA2" // 当前生效算法（只返回该算法的期望签名）
  algorithms: Record<string, { sign?: string; note?: string }>
}

export interface SettingItem {
  key: string
  value: unknown
  default: unknown
  type: string // string | text | int | float | decimal | bool | list | list_int
  group: string
  label: string
  description?: string
  updated_at: string | null
}

export const accountApi = {
  // ---- 商户端密钥/安全 ----
  apiKey: () => apiClient.get("/account/apikey") as Promise<ApiKeyData>,

  // 商户端「支付通道」卡片：所有启用中的通道编码 + 各自可用的支付方式
  channelCodes: () => apiClient.get("/account/channel-codes") as Promise<MerchantChannelCode[]>,

  // ---- admin 支付设置（KV） ----
  paySettings: () => apiClient.get("/setting") as Promise<{ items: SettingItem[] }>,

  savePaySettings: (values: Record<string, unknown>) =>
    apiClient.patch("/setting", values),

  // 生成平台 RSA 密钥对（私钥为 PKCS#8 DER Base64，与 platform_rsa_private_key 存储格式一致）
  generateRsaKeypair: () =>
    apiClient.post("/setting/generate-rsa-keypair") as Promise<{ private_key: string; public_key: string }>,

  // ---- admin 系统信息 ----
  // version/revision/build_date 来自镜像构建期（发版标签），源码运行为 dev
  systemInfo: () =>
    apiClient.get("/systemInfo") as Promise<{
      version: string
      revision?: string
      build_date?: string
      framework: string
      merchant_count: number
      admin_count: number
    }>,
}

export const securityApi = {
  // 一键生成 RSA 密钥对：公钥自动保存到商户资料，私钥仅此一次返回（平台不保存）
  // 公私钥均为无头尾单行 base64
  generateRsaPair: () =>
    apiClient.post("/account/rsa/generate") as Promise<{ public_key: string; private_key: string }>,

  // 重置 MD5 签名密钥（旧密钥立即失效）
  regenerateApiSecret: () =>
    apiClient.post("/account/apikey/secret") as Promise<{ api_secret: string }>,

  // 开启/关闭 RSA2 签名（开启前需配置公钥）
  toggleRsa2: (enable: boolean) =>
    apiClient.post("/account/rsa2", { enable }) as Promise<{ rsa2_enable: boolean }>,

  // 签名自检：回显签名原文 + 已开通算法的期望签名
  signDebug: (params: Record<string, unknown>) =>
    apiClient.post("/account/sign-debug", { params }) as Promise<SignDebugResult>,
}
