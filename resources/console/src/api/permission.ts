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

/** 同步预览里的「将新增」项 */
export interface PermissionSyncCreated {
  key: string
  name: string
  is_group: boolean
  group_key: string
  group_name: string
}

/** 同步预览里的「将变更」项（改名 / 换分组） */
export interface PermissionSyncUpdated extends PermissionSyncCreated {
  before_name: string
  before_group_name: string | null
  name_changed: boolean
  group_changed: boolean
}

/** 同步预览里的「将删除」项（树里已不存在的权限） */
export interface PermissionSyncRemoved {
  key: string
  name: string
}

export interface PermissionSyncPreview {
  created: PermissionSyncCreated[]
  updated: PermissionSyncUpdated[]
  removed: PermissionSyncRemoved[]
  created_count: number
  updated_count: number
  removed_count: number
}

export interface PermissionSyncResult {
  created: number
  updated: number
  removed: number
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

  /** 权限同步预览（只读，不写库） */
  previewSync: () =>
    apiClient.get("/permission/sync/preview") as Promise<PermissionSyncPreview>,

  /** 按代码里的权限树同步数据库（树里没有的权限连授权一起删除） */
  sync: () =>
    apiClient.post("/permission/sync") as Promise<PermissionSyncResult>,
}
