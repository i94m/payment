import { IconFileUnknown } from "@tabler/icons-react"
import { ErrorPage, GoHomeButton } from "./error-page"

export function Error404() {
  return (
    <ErrorPage
      icon={<IconFileUnknown className="size-16" />}
      title="页面不存在"
      description="抱歉，您访问的页面不存在或已被移除"
    >
      <GoHomeButton />
    </ErrorPage>
  )
}
