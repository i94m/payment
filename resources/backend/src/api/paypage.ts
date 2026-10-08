import apiClient from "@/lib/api-client"

export interface PaypageSettings {
  enable: boolean
  random_enable: boolean
  random_min: string
  random_max: string
  random_ratelimit: number
  link: string
}

export const paypageApi = {
  settings: () => apiClient.get("/paypage/settings") as Promise<PaypageSettings>,

  update: (payload: { random_enable: boolean; random_min: string; random_max: string; random_ratelimit: number }) =>
    apiClient.put("/paypage/settings", payload),
}
