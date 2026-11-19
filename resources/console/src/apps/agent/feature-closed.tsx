import { IconPlugConnectedX } from "@tabler/icons-react"
import { ErrorPage } from "@/pages/errors/error-page"
import { useSiteStore } from "@/stores/site-store"

/** 下级代理总开关（agent_enable）关闭时的代理面板落地页。
 *
 * 不渲染登录页与业务界面：此时后端也拒绝签发会话 / 拒绝业务接口，
 * 留在登录页只会让代理反复试密码。
 */
export function AgentFeatureClosed() {
  const siteName = useSiteStore((s) => s.siteName)
  return (
    <div className="flex min-h-screen flex-col">
      <ErrorPage
        icon={<IconPlugConnectedX className="size-16" />}
        title="下级代理功能已关闭"
        description={`${siteName || "平台"}暂未开放下级代理功能，请联系您的上级商户或平台管理员`}
      />
    </div>
  )
}
