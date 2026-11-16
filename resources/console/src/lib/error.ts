const MESSAGE_KEYS = ["message", "detail", "error", "errors", "msg"] as const

/**
 * 从任意错误对象中提取可展示的错误信息，取不到时返回 fallback。
 *
 * 兼容后端 `err()` 风格错误体 `{ code, message, errors }`、FastAPI 的
 * `{ detail: string | [] }`，以及 axios / 原生 Error / 字符串等形态。
 * 本项目的 api-client 已把响应体与 HTTP status 摊平后 reject（`{ code, message, status }`），
 * 归一化逻辑因此同时兼容 axios 原始形态（`error.response.data`）。
 */
export function getErrorMessage(error: unknown, fallback: string): string {
  return extractMessage(error, 0) ?? fallback
}

/** 是否为 HTTP 403（无权限）错误 */
export function isForbiddenError(error: unknown): boolean {
  return getErrorStatus(error) === 403
}

/** 取 HTTP status（摊平后的 `status` 或 axios 的 `response.status`） */
export function getErrorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== "object") return undefined
  const record = error as { status?: unknown; response?: { status?: unknown } }
  const flat = record.status ?? record.response?.status
  return typeof flat === "number" ? flat : undefined
}

/** 取业务错误码（后端包络里的 code，非 0 即失败） */
export function getErrorCode(error: unknown): number | undefined {
  if (!error || typeof error !== "object") return undefined
  const code = (error as { code?: unknown }).code
  return typeof code === "number" ? code : undefined
}

function extractMessage(value: unknown, depth: number): string | undefined {
  if (value == null || depth > 4) return undefined

  if (typeof value === "string") return normalize(value)
  if (value instanceof Error) return normalize(value.message)

  if (Array.isArray(value)) {
    for (const item of value) {
      const message = extractMessage(item, depth + 1)
      if (message) return message
    }
    return undefined
  }

  if (typeof value === "object") {
    const record = value as Record<string, unknown>
    for (const key of MESSAGE_KEYS) {
      const message = extractMessage(record[key], depth + 1)
      if (message) return message
    }
    // 兼容 `{ errors: { field: ["msg"] } }` 之类的映射结构
    for (const item of Object.values(record)) {
      const message = extractMessage(item, depth + 1)
      if (message) return message
    }
  }

  return undefined
}

function normalize(message: string): string | undefined {
  const text = message.trim()
  if (!text) return undefined
  if (/^Network Error$/i.test(text)) return "网络请求失败，请检查网络连接"
  if (/timeout of \d+ms exceeded/i.test(text)) return "请求超时，请稍后重试"
  return text
}
