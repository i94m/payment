import type { ReactNode } from "react"
import { useSiteStore } from "@/stores/site-store"
import type { SiteFeatures } from "@/stores/site-store"
import { Error404 } from "@/pages/errors/404"

/** 功能开关门控路由：站点开关关闭时该页面按「不存在」处理（直访 URL 也不渲染业务界面）。
 *
 * 只负责展示层：真正的关闭点在接口层（如 agent_enable → 商户端/代理端路由 403）。
 * 开关未拉取（/site-info 未返回）前不渲染页面：否则默认值（开启）会让页面先发一轮
 * 业务请求，拿到 403 后被全局拦截器跳到 403 页，而不是这里的 404。
 */
export function FeatureRoute({ feature, children }: {
  feature: keyof SiteFeatures
  children: ReactNode
}) {
  const loaded = useSiteStore((s) => s.loaded)
  const features = useSiteStore((s) => s.features)
  if (!loaded) return null
  if (features[feature] === false) return <Error404 />
  return <>{children}</>
}
