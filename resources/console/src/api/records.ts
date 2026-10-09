import apiClient from "@/lib/api-client"
import type { PaginatedResponse } from "./index"

export interface MerchantRecordData {
  id: number
  biz_type: string
  amount: string
  balance_before: string
  balance_after: string
  order_trade_no: string
  remark: string
  created_at: string
}

export interface MerchantSettlementData {
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

export const recordsApi = {
  list: (params?: { page?: number; per_page?: number }) =>
    apiClient.get("/record/list", { params }) as Promise<PaginatedResponse<MerchantRecordData>>,

  settlements: (params?: { page?: number; per_page?: number }) =>
    apiClient.get("/record/settlements", { params }) as Promise<PaginatedResponse<MerchantSettlementData>>,
}
