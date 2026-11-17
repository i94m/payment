/**
 * 支付方式内置图标库
 *
 * 图标文件位于后端 static/icons/pay（挂载在 /icons），源码入库、随镜像下发，
 * 因此可以直接用站内绝对路径引用（三个 SPA + 收银台页共用同一份资源）。
 * 后台「支付方式」表单从这里选择，payment_types.icon 存的也是这个路径；
 * 订单等只带 code 的场景按 code 兜底取内置图标。
 */

export interface PayIconOption {
  /** 对应 payment_types.code */
  code: string
  label: string
  /** 图标地址（站内绝对路径） */
  src: string
}

export const PAY_ICON_LIBRARY: PayIconOption[] = [
  { code: "alipay", label: "支付宝", src: "/icons/pay/alipay.svg" },
  { code: "wxpay", label: "微信支付", src: "/icons/pay/wxpay.svg" },
  { code: "qqpay", label: "QQ钱包", src: "/icons/pay/qqpay.svg" },
  { code: "bank", label: "网银支付", src: "/icons/pay/bank.svg" },
  { code: "jdpay", label: "京东支付", src: "/icons/pay/jdpay.svg" },
  { code: "paypal", label: "PayPal", src: "/icons/pay/paypal.svg" },
]

/** 新增支付方式时默认选中的图标（支付宝，与图标库第一项一致） */
export const DEFAULT_PAY_ICON = PAY_ICON_LIBRARY[0].src

const BY_CODE = new Map(PAY_ICON_LIBRARY.map((it) => [it.code, it.src]))

/** 支付方式图标地址：优先后台配置的 icon，其次按 code 取内置图标，都没有返回空串 */
export function payIconSrc(code?: string | null, icon?: string | null): string {
  const custom = (icon ?? "").trim()
  if (custom) return custom
  return BY_CODE.get(String(code ?? "").trim().toLowerCase()) ?? ""
}
