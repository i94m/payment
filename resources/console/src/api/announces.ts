import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface AnnounceData {
  id: number
  content: string
  status: number // 1=发布 0=下线
  created_at: string
  updated_at: string
}

export const announcesApi = {
  list: (params?: Record<string, unknown>) =>
    apiClient.post("/announce/list", params) as Promise<PaginatedResponse<AnnounceData>>,

  create: (payload: { content: string; status: number }) =>
    apiClient.post("/announce", payload) as Promise<AnnounceData>,

  update: (id: number, payload: { content: string; status: number }) =>
    apiClient.put(`/announce/${id}`, payload) as Promise<AnnounceData>,

  remove: (id: number) => apiClient.delete(`/announce/${id}`),
}
