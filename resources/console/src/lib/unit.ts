/** 标签末尾的纯单位括注：「结算最低金额（元）」→ 标签「结算最低金额」+ 单位「元」 */
const UNIT_LABEL_RE = /^(.*?)[（(]\s*([^()（）]{1,8})\s*[)）]$/

/** 只搬运明确的计量单位，普通括注（如「站点地址（用于…）」）保持原样 */
const KNOWN_UNITS = new Set(["元", "%", "分钟", "小时", "天", "秒", "次", "笔"])

/**
 * 把「名称（单位）」拆成名称和单位，供输入框把单位显示在后缀里。
 * 括注不是已知单位时原样返回，避免误伤说明性文字。
 */
export function splitUnitLabel(label: string): { label: string; unit?: string } {
  const match = UNIT_LABEL_RE.exec(label.trim())
  if (!match) return { label }
  const unit = match[2].trim()
  if (!KNOWN_UNITS.has(unit)) return { label }
  return { label: match[1].trim(), unit }
}
