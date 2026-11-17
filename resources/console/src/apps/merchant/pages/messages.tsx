"use client"

import * as React from "react"
import { messagesApi } from "@/api"
import type { MessageData } from "@/api"
import { Button } from "@/components/ui/button"
import { FilterCard } from "@/components/filter-card"
import { Badge } from "@/components/ui/badge"
import { IconCheck, IconChevronLeft, IconChevronRight } from "@tabler/icons-react"
import { toast } from "sonner"

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
  const perPage = 15
  const totalPages = Math.ceil(total / perPage) || 1

  const load = React.useCallback(() => {
    setLoading(true)
    messagesApi.list({ page, per_page: perPage })
      .then((res) => { setData(res.items); setTotal(res.total) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [page])

  React.useEffect(() => { load() }, [load])

  const readAll = async () => {
    try {
      await messagesApi.read(0)
      toast.success("已全部标记为已读")
      load()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6">
        <FilterCard className="flex-row justify-end">
          <Button size="sm" variant="outline" onClick={readAll}>
            <IconCheck className="size-4" />
            {"全部已读"}
          </Button>
        </FilterCard>

        <div className="px-4 lg:px-6 space-y-2">
          {loading && data.length === 0 && (
            <p className="text-sm text-muted-foreground py-6 text-center">...</p>
          )}
          {!loading && data.length === 0 && (
            <p className="text-sm text-muted-foreground py-6 text-center">{"暂无数据"}</p>
          )}
          {data.map((m) => (
            <div key={m.id} className={`rounded-lg border p-3 ${m.is_read ? "opacity-70" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  {!m.is_read && <span className="size-2 rounded-full bg-primary" />}
                  <span className="text-sm font-medium">{m.title}</span>
                  <Badge variant="outline" className={bizCls[m.biz_type] ?? ""}>
                    {m.biz_type_name || m.biz_type}
                  </Badge>
                </div>
                <span className="text-xs text-muted-foreground whitespace-nowrap">{m.created_at}</span>
              </div>
              {m.content && <p className="mt-1 text-sm text-muted-foreground">{m.content}</p>}
            </div>
          ))}
          {totalPages > 1 && (
            <div className="flex items-center justify-end gap-2 pt-2">
              <Button size="sm" variant="outline" disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}>
                <IconChevronLeft className="size-4" />
              </Button>
              <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
              <Button size="sm" variant="outline" disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>
                <IconChevronRight className="size-4" />
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
