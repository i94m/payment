"use client"

import * as React from "react"
import { messagesApi } from "@/api"
import type { MessageData } from "@/api"
import { Button } from "@/components/ui/button"
import { FilterCard } from "@/components/filter-card"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty"
import { PanelError } from "@/components/data-list"
import { getErrorMessage } from "@/lib/error"
import { IconCheck, IconChevronLeft, IconChevronRight, IconInbox } from "@tabler/icons-react"
import { toast } from "sonner"

const BIZ_LABELS: Record<string, string> = {
  settle: "结算",
  recharge: "充值",
  system: "系统",
  order: "订单",
  refund: "退款",
}

const bizCls: Record<string, string> = {
  settle: "bg-primary/10 text-primary",
  recharge: "bg-secondary text-secondary-foreground",
  system: "bg-secondary text-secondary-foreground",
}

export function MessagesPage() {
  const [data, setData] = React.useState<MessageData[]>([])
  const [total, setTotal] = React.useState(0)
  const [page, setPage] = React.useState(1)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState<string | null>(null)
  const [readingAll, setReadingAll] = React.useState(false)
  const perPage = 15
  const totalPages = Math.ceil(total / perPage) || 1

  const load = React.useCallback(() => {
    messagesApi.list({ page, per_page: perPage })
      .then((res) => { setData(res.items); setTotal(res.total) })
      .catch((err) => setError(getErrorMessage(err, "消息加载失败")))
      .finally(() => setLoading(false))
  }, [page])

  React.useEffect(() => { load() }, [load])

  // 加载态/错误态只在交互入口同步置位（不在 effect 内同步 setState）
  const reload = () => { setLoading(true); setError(null); load() }
  const changePage = (p: number) => { setLoading(true); setError(null); setPage(p) }

  const unreadCount = data.filter((m) => !m.is_read).length

  const readAll = async () => {
    setReadingAll(true)
    try {
      await messagesApi.read(0)
      toast.success("已全部标记为已读")
      reload()
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setReadingAll(false)
    }
  }

  /** 点击未读消息即标记已读（无需再进详情页） */
  const readOne = async (message: MessageData) => {
    if (message.is_read) return
    try {
      await messagesApi.read(message.id)
      setData((prev) => prev.map((m) => (m.id === message.id ? { ...m, is_read: true } : m)))
    } catch (err) {
      toast.error(getErrorMessage(err, "标记已读失败"))
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard className="flex-row items-center justify-between">
          <p className="text-sm text-muted-foreground">
            {unreadCount > 0 ? `本页 ${unreadCount} 条未读` : "本页消息均已读"}
          </p>
          <Button size="sm" variant="outline" disabled={readingAll || (loading && data.length === 0)} onClick={readAll}>
            <IconCheck className="size-4" />
            {"全部已读"}
          </Button>
        </FilterCard>

        <div className="px-4 lg:px-6 space-y-2">
          {error && <PanelError error={error} onRetry={reload} />}

          {loading && data.length === 0 && (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="rounded-lg border p-3">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="mt-2 h-3 w-full max-w-md" />
                </div>
              ))}
            </div>
          )}

          {!loading && !error && data.length === 0 && (
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <IconInbox />
                </EmptyMedia>
                <EmptyTitle>{"暂无消息"}</EmptyTitle>
                <EmptyDescription>{"平台公告、结算与退款通知会出现在这里"}</EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}

          {data.map((m) => (
            <div
              key={m.id}
              className={`rounded-lg border p-3 transition-colors ${m.is_read ? "opacity-70" : "cursor-pointer hover:bg-muted/40"}`}
              onClick={() => { void readOne(m) }}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  {!m.is_read && <span className="size-2 shrink-0 rounded-full bg-primary" />}
                  <span className={`truncate text-sm ${m.is_read ? "" : "font-medium"}`}>{m.title || "（无标题）"}</span>
                  <Badge variant="outline" className={`shrink-0 ${bizCls[m.biz_type] ?? ""}`}>
                    {m.biz_type_name || BIZ_LABELS[m.biz_type] || m.biz_type}
                  </Badge>
                </div>
                <span className="whitespace-nowrap text-xs text-muted-foreground">{m.created_at}</span>
              </div>
              {m.content && <p className="mt-1 whitespace-pre-wrap break-all text-sm text-muted-foreground">{m.content}</p>}
            </div>
          ))}

          {totalPages > 1 && (
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button size="sm" variant="outline" disabled={page <= 1}
                onClick={() => changePage(Math.max(1, page - 1))}>
                <IconChevronLeft className="size-4" />
                {"上一页"}
              </Button>
              <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
              <Button size="sm" variant="outline" disabled={page >= totalPages}
                onClick={() => changePage(Math.min(totalPages, page + 1))}>
                {"下一页"}
                <IconChevronRight className="size-4" />
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
