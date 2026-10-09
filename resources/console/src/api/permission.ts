import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface AdminAccountData {
  id: number
  username: string
  name: string
  email: string
  is_forbid: boolean
  is_super_admin: boolean
  created_at: string
  permissions?: number[]
  permission_details?: { id: number; name: string; key: string }[]
}

export interface PermissionNode {
  id: number
  pid: number
  name: string
  key: string
  children: { id: number; pid: number; name: string; key: string }[]
}

export interface AccountPayload {
  username: string
  name?: string
  password?: string
  is_forbid?: boolean | null
  permissions?: number[] | null
}

export const permissionApi = {
  accounts: (params?: { status?: string; keyword?: string; page?: number; per_page?: number }) =>
    apiClient.get("/permission/accounts", { params }) as Promise<PaginatedResponse<AdminAccountData>>,

  accountDetail: (id: number) => apiClient.get(`/permission/accounts/${id}`) as Promise<AdminAccountData>,

  createAccount: (data: AccountPayload) =>
    apiClient.post("/permission/accounts", data) as Promise<AdminAccountData>,

  updateAccount: (id: number, data: AccountPayload) =>
    apiClient.put(`/permission/accounts/${id}`, data) as Promise<AdminAccountData>,

  deleteAccount: (id: number) => apiClient.delete(`/permission/accounts/${id}`),

  /** 权限树 [{id,pid,name,key,children:[…]}] */
  tree: () => apiClient.get("/permission/permissions") as Promise<PermissionNode[]>,
}
