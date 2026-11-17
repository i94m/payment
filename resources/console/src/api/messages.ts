import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface MessageData {
  id: number
  title: string
  content: string
  biz_type: string
  biz_type_name?: string
  is_read: boolean
  created_at: string
}

export const messagesApi = {
  list: (params?: { page?: number; per_page?: number; unread?: number }) =>
    apiClient.get("/message/list", { params }) as Promise<PaginatedResponse<MessageData>>,

  unreadCount: () => apiClient.get("/message/unread-count") as Promise<{ count: number }>,

  read: (id = 0) => apiClient.post("/message/read", { id }),
}
