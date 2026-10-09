import { z } from "zod"

/**
 * 数值字符串校验（表单输入均为字符串，提交前再转数值）。
 * 语义对齐原 validateFields：留空时仅必填报错，可选字段跳过后续校验。
 *
 * 用法：
 *   merchant_rate: numStr("商户费率", { min: 0, max: 100 })          // 必填
 *   day_limit: numStr("日限额", { min: 0, optional: true })          // 可选，留空不限
 *   day_limit_count: numStr("单日限笔", { min: 0, integer: true, optional: true })
 */
export function numStr(
  label: string,
  opts: { min?: number; max?: number; integer?: boolean; optional?: boolean } = {},
) {
  return z.string().superRefine((v, ctx) => {
    const s = (v ?? "").trim()
    if (!s) {
      if (!opts.optional) ctx.addIssue(`${label}不能为空`)
      return
    }
    const n = Number(s)
    if (Number.isNaN(n)) {
      ctx.addIssue(`${label}需为有效数字`)
      return
    }
    if (opts.integer && !Number.isInteger(n)) {
      ctx.addIssue(`${label}需为整数`)
      return
    }
    if (opts.min !== undefined && n < opts.min) {
      ctx.addIssue(`${label}不能小于 ${opts.min}`)
      return
    }
    if (opts.max !== undefined && n > opts.max) {
      ctx.addIssue(`${label}不能大于 ${opts.max}`)
    }
  })
}
