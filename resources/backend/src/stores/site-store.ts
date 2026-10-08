import { create } from "zustand"
import apiClient from "@/lib/api-client"

// /site-info 下发的功能开关（商户端菜单显隐），与后端 PUBLIC_FEATURE_KEYS 白名单对齐。
// fetch 前用 DEFAULT_SITE_FEATURES（全量渲染），避免菜单闪空。
export interface SiteFeatures {
  agent_enable: boolean
  recharge_enable: boolean
  paypage_enable: boolean
  api_pay_enable: boolean
  site_qq_group: string
  site_app_url: string
}

export const DEFAULT_SITE_FEATURES: SiteFeatures = {
  agent_enable: true,
  recharge_enable: true,
  paypage_enable: true,
  api_pay_enable: true,
  site_qq_group: "",
  site_app_url: "",
}

interface SiteState {
  siteName: string
  features: SiteFeatures
  fetchSite: () => Promise<void>
}

// 站点名称（后台「支付设置-站点名称」）：登录页标题 / 侧边栏品牌 / 浏览器标签页。
// features：后台菜单显隐开关，商户端侧边栏/⌘K 按其过滤。
// /site-info 为公开端点（admin-api 与 merchant-api 双前缀挂载），未登录也可读取。
export const useSiteStore = create<SiteState>((set) => ({
  siteName: "",
  features: DEFAULT_SITE_FEATURES,
  fetchSite: async () => {
    try {
      const res = await apiClient.get("/site-info") as {
        site_name?: string
        features?: Partial<SiteFeatures>
      }
      // 每次 set 都放新对象引用，保证订阅 features 的组件（侧边栏）重渲染
      set({
        siteName: (res.site_name || "").trim(),
        features: { ...DEFAULT_SITE_FEATURES, ...(res.features ?? {}) },
      })
    } catch {
      // 静默失败：展示层用默认名兜底，菜单按默认全量渲染
    }
  },
}))
