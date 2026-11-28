import apiClient from "@/lib/api-client"

export interface PaypageSettings {
  enable: boolean
  random_enable: boolean
  random_min: string
  random_max: string
  random_integer: boolean   // 仅整数金额（关闭时支持两位小数）
  random_ratelimit: number
  link: string
}

export const paypageApi = {
  settings: () => apiClient.get("/paypage/settings") as Promise<PaypageSettings>,

  update: (payload: {
    random_enable: boolean
    random_min: string
    random_max: string
    random_integer?: boolean
    random_ratelimit: number
  }) =>
    apiClient.put("/paypage/settings", payload),
}
