import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { IconRefresh } from "@tabler/icons-react"

/**
 * 页面级加载失败提示（带重试）。
 *
 * 只读数据加载失败时不要只弹一个 toast 就留一屏空白/骨架：错误要停留在页面上
 * 可读、可操作。列表页的失败态由 useDataList 的 onError 弹 toast 处理，这里是
 * dashboard / 详情 / 表单初始化这类「整块数据」的失败态。
 */
export function LoadError({
  message,
  onRetry,
  className,
}: {
  message: string
  onRetry?: () => void
  className?: string
}) {
  return (
    <Alert variant="destructive" className={className}>
      <AlertDescription className="mt-0 flex flex-wrap items-center justify-between gap-2">
        <span className="break-all">{message}</span>
        {onRetry && (
          <Button type="button" size="sm" variant="outline" onClick={onRetry}>
            <IconRefresh className="size-3.5" />
            {"重试"}
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}
