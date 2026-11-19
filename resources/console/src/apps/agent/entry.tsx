import { useEffect } from "react"
import App from "@/App.tsx"
import { useSiteStore } from "@/stores/site-store"
import { routes } from "./routes"
import { getAgentMenu } from "./sidebar"
import { AgentFeatureClosed } from "./feature-closed"

/** 代理面板入口：受站点开关 agent_enable 门控。
 *
 * 关闭时不渲染登录页/业务界面（后端同时拒绝登录、SSO 与业务接口），
 * 避免代理在登录页反复尝试却发现密码「错误」。
 */
export function AgentEntry() {
  const loaded = useSiteStore((s) => s.loaded)
  const features = useSiteStore((s) => s.features)
  const fetchSite = useSiteStore((s) => s.fetchSite)

  // App 未挂载时 App.tsx 内的 fetchSite 不会执行，这里补一次
  useEffect(() => {
    if (!loaded) void fetchSite()
  }, [loaded, fetchSite])

  // 开关未拉取前留空，避免已关闭时先闪一下登录页
  if (!loaded) return null
  if (!features.agent_enable) return <AgentFeatureClosed />
  return <App routes={routes} getMenu={() => getAgentMenu()} />
}
