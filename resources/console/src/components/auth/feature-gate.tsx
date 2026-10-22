import type { ReactNode } from "react"
import { useSiteStore, type SiteFeatures } from "@/stores/site-store"
import { Error404 } from "@/pages/errors/404"

/**
 * 运行期功能开关（settings 表可改）控制的路由守卫。
 *
 * /site-info 拉取完成前不渲染（避免默认值让已关闭功能的页面闪现），
 * 关闭后直链落到 404 —— 页面组件与接口代码均保留，开关打开即恢复。
 */
export function FeatureGate({ feature, children }: {
  feature: keyof SiteFeatures
  children: ReactNode
}) {
  const loaded = useSiteStore((s) => s.loaded)
  const enabled = useSiteStore((s) => s.features[feature])
  if (!loaded) return null
  return enabled ? <>{children}</> : <Error404 />
}
