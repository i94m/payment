import { IconAlertTriangle, IconRefresh } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import { getErrorMessage } from "@/lib/error"
import { cn } from "@/lib/utils"

interface ListErrorProps {
  /** 列表请求失败的错误对象（useDataList 的 error） */
  error: unknown
  /** 重试回调，通常直接传 useDataList 的 refresh */
  onRetry?: () => void
  title?: string
  className?: string
}

/**
 * 列表加载失败态：替换表格区域展示，带后端真实失败原因与重试按钮。
 * 与「暂无数据」空态区分开——请求失败时不能再显示空表格，否则会被误读为没有数据。
 */
export function ListError({ error, onRetry, title = "数据加载失败", className }: ListErrorProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-10 text-center",
        className,
      )}
    >
      <IconAlertTriangle className="size-6 text-destructive" />
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-xl text-xs break-words text-muted-foreground">
        {getErrorMessage(error, "请稍后重试")}
      </p>
      {onRetry && (
        <Button size="sm" variant="outline" className="mt-1" onClick={onRetry}>
          <IconRefresh className="size-4" />
          {"重试"}
        </Button>
      )}
    </div>
  )
}

/** 表单/详情区块的加载失败态（比列表更紧凑），用于弹窗内取数失败 */
export function PanelError({ error, onRetry, className }: Omit<ListErrorProps, "title"> & { title?: string }) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-6 text-center",
        className,
      )}
    >
      <IconAlertTriangle className="size-5 text-destructive" />
      <p className="max-w-md text-xs break-words text-muted-foreground">
        {getErrorMessage(error, "加载失败，请稍后重试")}
      </p>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <IconRefresh className="size-4" />
          {"重试"}
        </Button>
      )}
    </div>
  )
}
