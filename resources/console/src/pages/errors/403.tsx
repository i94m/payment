import { IconShieldX } from "@tabler/icons-react"
import { ErrorPage } from "./error-page"

export function Error403() {
  return (
    <ErrorPage
      icon={<IconShieldX className="size-16" />}
      title="无访问权限"
      description="抱歉，您没有访问该页面的权限，请联系管理员"
    />
  )
}
