import apiClient from "@/lib/api-client"
import { APP_MODULE } from "@/lib/config"
import type { PaginatedResponse } from "./index"

export interface OrderData {
  id?: number
  trade_no: string
  out_trade_no: string
  api_trade_no?: string
  merchant_id?: number
  merchant_username?: string
  /** 订单类型：0=正式收款 1=余额充值 2=测试单 */
  order_type?: number
  type: string
  /** 支付方式显示名称（后端按 payment_types 解析；缺失时用 type 调用值兜底） */
  type_name?: string
  /** 支付方式图标 URL（后台配置；为空时前端按 type 取内置图标库兜底） */
  type_icon?: string
  channel_id?: number
  /** 下游通道编码（下单时下游传的值，留痕；空=未指定编码） */
  channel_code?: string
  channel_code_id?: number
  plugin_name?: string
  subject: string
  money: string
  realmoney: string
  merchant_income: string
  channel_cost?: string
  profit?: string
  notify_url?: string
  return_url?: string
  param?: string
  status: number
  /** 扣量单：命中扣量（不入账、不返佣、不通知；后台可见，商户/代理端不可见） */
  is_deducted?: boolean
  /** 退款状态：NONE 未退 / PARTIAL 部分退 / FULL 全额退（仅详情接口返回） */
  refund_status?: string
  /** 累计已退金额（仅详情接口返回） */
  refunded_amount?: string
  notify_status?: number
  notify_count?: number
  next_notify_at?: string
  paid_at: string
  expire_at?: string
  created_at: string
  client_ip?: string
  buyer_id?: string
}

export interface OrderListParams {
  page?: number
  per_page?: number
  trade_no?: string
  out_trade_no?: string
  merchant_id?: number | ""
  status?: number | ""
  type?: string
  /**
   * 订单类型：""（默认，全部类型，含测试单）/ "0" 正式收款 / "1" 余额充值 / "2" 测试单。
   * 商户端只认 ""（正式收款 + 测试单）、"0" 与 "2"。
   */
  order_type?: string
  notify_status?: number | ""
  /** "start,end"（YYYY-MM-DD） */
  addtime?: string
  /** 扣量筛选（admin）：""=全部 / "1"=仅扣量 / "0"=仅未扣量 */
  is_deducted?: "" | "0" | "1"
  /** 下游通道编码筛选（admin）：按编码 ID */
  channel_code_id?: number
  channel_code?: string
}

export const ordersApi = {
  /** admin 与 merchant 同为 POST /order/list，字段范围由后端按身份裁剪 */
  list: (params: OrderListParams & Record<string, unknown>) =>
    apiClient.post("/order/list", params) as Promise<PaginatedResponse<OrderData>>,

  detail: (tradeNo: string) => apiClient.get(`/order/${tradeNo}`) as Promise<OrderData>,

  /** 手动补单（仅 admin） */
  refill: (tradeNo: string) =>
    apiClient.post("/order/refill", { trade_no: tradeNo }),

  /** 重发商户通知：admin → /order/notify {trade_no}，merchant → /order/{trade_no}/notify */
  resendNotify: (tradeNo: string) =>
    APP_MODULE === "merchant"
      ? apiClient.post(`/order/${tradeNo}/notify`)
      : apiClient.post("/order/notify", { trade_no: tradeNo }),

  /** 导出 Excel（blob，由调用方触发下载；仅 admin） */
  exportUrl: "/order/export",
}
