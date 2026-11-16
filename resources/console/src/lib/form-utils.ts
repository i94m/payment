import type { ZodTypeAny } from "zod"

// 这些基础类型的控件不存在「留空」语义（开关/勾选/数组/日期始终有值），
// schema 拒绝 undefined 在它们身上是误报，永不显示必填星号
const NEVER_ASTERISK = new Set([
  "boolean",
  "array",
  "object",
  "date",
  "optional",
  "default",
])

/**
 * 从 zod object schema 推导每个字段是否必填（用于渲染红色星号）。
 * 判定：字段同时拒绝 undefined 和 "" 才算必填 —— 仅检查 undefined 会把
 * z.string() / z.coerce.number() 等全部误标（它们都拒绝 undefined）。
 * schema 经过 .refine/.superRefine 后 .shape 仍在（zod v4 返回同类型）；
 * 经过 .transform() 后 shape 丢失，安全降级为无星号。
 */
export function deriveRequiredFields(schema: unknown): Record<string, boolean> {
  const shape = (schema as { shape?: Record<string, ZodTypeAny> } | null | undefined)
    ?.shape
  if (!shape) return {}

  const out: Record<string, boolean> = {}
  for (const [key, field] of Object.entries(shape)) {
    const base = (
      field as { _zod?: { def?: { type?: string } } } | undefined
    )?._zod?.def?.type
    if (!base || NEVER_ASTERISK.has(base)) {
      out[key] = false
      continue
    }
    out[key] = !field.safeParse(undefined).success && !field.safeParse("").success
  }
  return out
}

/** 只保留数字字符：ID 类输入框的即时清洗（避免把 "abc" 当商户号提交） */
export function digitsOnly(value: string): string {
  return value.replace(/\D/g, "")
}

/**
 * 安全整数参数：空串/非数字/非有限值一律返回 undefined。
 * 列表筛选参数走这里，避免 `Number("abc") => NaN` 被序列化成 `?merchant_id=NaN`。
 */
export function toIntParam(value: unknown): number | undefined {
  if (value == null) return undefined
  const raw = typeof value === "string" ? value.trim() : value
  if (raw === "") return undefined
  const n = typeof raw === "number" ? raw : Number(raw)
  return Number.isFinite(n) ? Math.trunc(n) : undefined
}

/** 安全数值参数（保留小数，如金额筛选）；非法值为 undefined */
export function toNumberParam(value: unknown): number | undefined {
  if (value == null) return undefined
  const raw = typeof value === "string" ? value.trim() : value
  if (raw === "") return undefined
  const n = typeof raw === "number" ? raw : Number(raw)
  return Number.isFinite(n) ? n : undefined
}
