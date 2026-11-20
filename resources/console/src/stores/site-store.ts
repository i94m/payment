import { create } from "zustand"
import apiClient from "@/lib/api-client"

// /site-info 下发的功能开关（商户端菜单显隐），与后端 PUBLIC_FEATURE_KEYS 白名单对齐。
// fetch 前用 DEFAULT_SITE_FEATURES（全量渲染），避免菜单闪空。
export interface SiteFeatures {
  agent_enable: boolean
  announce_enable: boolean
  merchant_records_enable: boolean
  recharge_enable: boolean
  paypage_enable: boolean
  api_pay_enable: boolean
  merchant_reg_open: boolean
  site_qq_group: string
  site_app_url: string
}

export const DEFAULT_SITE_FEATURES: SiteFeatures = {
  agent_enable: true,
  announce_enable: true,
  merchant_records_enable: true,
  recharge_enable: true,
  paypage_enable: true,
  api_pay_enable: true,
  merchant_reg_open: true,
  site_qq_group: "",
  site_app_url: "",
}

interface SiteState {
  siteName: string
  /** 镜像版本（构建期写入，源码运行为 dev）：后台侧边栏站点名称下方展示 */
  version: string
  /** 镜像构建的提交号，作为版本号的 tooltip 补充 */
  revision: string
  features: SiteFeatures
  /** /site-info 是否已拉取（含失败）：路由级开关判定前先等它，避免默认值闪现已关闭的功能 */
  loaded: boolean
  fetchSite: () => Promise<void>
}

// 站点名称（后台「站点配置-站点名称」）：登录页标题 / 侧边栏品牌 / 浏览器标签页。
// features：后台菜单显隐开关，商户端侧边栏/⌘K 按其过滤。
// /site-info 为公开端点（admin-api 与 merchant-api 双前缀挂载），未登录也可读取。
export const useSiteStore = create<SiteState>((set) => ({
  siteName: "",
  version: "",
  revision: "",
  features: DEFAULT_SITE_FEATURES,
  loaded: false,
  fetchSite: async () => {
    try {
      const res = await apiClient.get("/site-info") as {
        site_name?: string
        version?: string
        revision?: string
        features?: Partial<SiteFeatures>
      }
      // 每次 set 都放新对象引用，保证订阅 features 的组件（侧边栏）重渲染
      set({
        siteName: (res.site_name || "").trim(),
        version: (res.version || "").trim(),
        revision: (res.revision || "").trim(),
        features: { ...DEFAULT_SITE_FEATURES, ...(res.features ?? {}) },
        loaded: true,
      })
    } catch {
      // 静默失败：展示层用默认名兜底，菜单按默认全量渲染
      set({ loaded: true })
    }
  },
}))
