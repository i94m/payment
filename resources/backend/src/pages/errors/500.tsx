import { IconServerBolt } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import { ErrorPage, GoHomeButton } from "./error-page"

export function Error500() {
  return (
    <ErrorPage
      icon={<IconServerBolt className="size-16" />}
      title="服务器错误"
      description="抱歉，服务器出了点问题，请稍后重试"
    >
      <div className="flex gap-3">
        <Button variant="outline" onClick={() => window.location.reload()}>重试</Button>
        <GoHomeButton />
      </div>
    </ErrorPage>
  )
}
