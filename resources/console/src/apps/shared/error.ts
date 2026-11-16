/**
 * 商户/代理面板共用的错误文案提取。
 *
 * api-client 的失败分支会 reject 后端 `{code, message}` 包络，或 axios 的
 * `{code, message, status}`；这里统一把「需展示给用户的文案」抽出来，避免各页面
 * 各写一份 `(err as {message?: string})?.message || "操作失败"`，也顺手把
 * axios 的网络/超时英文文案转成中文。
 *
 * 说明：`@/lib/error`（lib owner 维护）落地后行为与此一致，届时可切换为从 lib
 * 导入并删除本文件，调用点无需改动。
 */

const MESSAGE_KEYS = ["message", "detail", "msg", "error"] as const

/** 从任意错误对象中提取可展示的文案，取不到时返回 fallback。 */
export function getErrorMessage(error: unknown, fallback: string): string {
  return extractMessage(error, 0) ?? fallback
}

/** 是否为 HTTP 403（无权限）——api-client 已统一跳 403 页，调用方通常无需再提示。 */
export function isForbiddenError(error: unknown): boolean {
  return (
    !!error &&
    typeof error === "object" &&
    (error as { status?: number }).status === 403
  )
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
