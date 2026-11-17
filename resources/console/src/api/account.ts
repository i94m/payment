import apiClient from "@/lib/api-client"

export interface ApiKeyData {
  pid: number
  public_key: string // 商户公钥（未生成为空串）
  platform_public_key: string // 平台公钥（由平台私钥派生，未配置为空串）
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

  // 保存商户 RSA 公钥（API 调用验签用）；传空即清除公钥
  setPublicKey: (public_key: string) =>
    apiClient.post("/account/publickey", { public_key }) as Promise<{ public_key: string }>,

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
}
