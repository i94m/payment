import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface AdminSettleData {
  id: number
  settle_no: string
  merchant_id: number
  merchant_username: string
  amount: string
  fee: string
  real_amount: string
  account: string
  account_name: string
  status: number // 0 待处理 / 1 已打款 / 2 已拒绝
  remark: string
  paid_at: string
  created_at: string
}

export interface MerchantSettleData {
  id: number
  settle_no: string
  amount: string
  fee: string
  real_amount: string
  account: string
  account_name: string
  status: number
  remark: string
  paid_at: string
  created_at: string
}

export interface SettleInfo {
  balance: string
  frozen_balance: string
  settle_account: string
  settle_name: string
  pending_count: number
}

export const settleApi = {
  // ---- admin ----
  adminList: (params?: { page?: number; per_page?: number; status?: number | ""; merchant_id?: number | "" }) =>
    apiClient.get("/settle/list", { params }) as Promise<PaginatedResponse<AdminSettleData>>,

  markPaid: (id: number, remark = "") =>
    apiClient.post(`/settle/${id}/pay`, { remark }),

  reject: (id: number, remark = "") =>
    apiClient.post(`/settle/${id}/reject`, { remark }),

  // ---- merchant ----
  info: () => apiClient.get("/settle/info") as Promise<SettleInfo>,

  apply: (amount: string) =>
    apiClient.post("/settle/apply", { amount }) as Promise<{ settle_no: string; amount: string }>,
}

export interface SettleInfoExtended extends SettleInfo {
  auto_settle: boolean
  fee_rate: string
  min_amount: string
  remain_money: string
  withdrawable: string
  fee_min: string
  fee_max: string
  d1_enable: boolean
  today_income: string
  daily_limit: number
  daily_used: number
  settle_type: number
  settle_type_name: string
  types: { id: number; code: string; name: string; icon: string | null }[]
}
