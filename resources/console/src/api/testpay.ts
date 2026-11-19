import apiClient from "@/lib/api-client"

/** 测试支付可选支付方式（启用且有可用通道，与收银台同口径） */
export interface TestPayType {
  id: number
  code: string
  name: string
  icon?: string | null
}

export interface TestPayResult {
  /** 测试单号（order_type=2，可在订单记录「测试单」筛选项里查到） */
  trade_no: string
  /** 插件返回的支付动作：jump / qrcode / scheme / html */
  pay_type: string
  /** 支付链接、二维码地址或 HTML 表单（按 pay_type 解释） */
  pay_info: string
}

export const testPayApi = {
  types: () => apiClient.get("/testpay/types") as Promise<TestPayType[]>,
  submit: (data: { type: string; amount: string; name: string }) =>
    apiClient.post("/testpay/submit", data) as Promise<TestPayResult>,
}
