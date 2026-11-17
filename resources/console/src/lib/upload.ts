import apiClient from "@/lib/api-client"
import { getFileUrl, setStorageBase } from "@/lib/storage"

/** 后端 `/upload/sts` 返回的策略与临时上传配置 */
export interface StsConfig {
  provider: string
  prefix: string
  upload_url: string
  url: string
  payload: Record<string, string | number>
  extensions: string[]
  max_file_size: number
  maximum: number
  success_action_status: number
  withCredentials?: boolean
}

export interface UploadOptions {
  /** 上传策略名，对应后端 config.UPLOAD_POLICIES */
  policy?: string
  /** 上传进度（0-100） */
  onProgress?: (percent: number) => void
  /** 取消上传 */
  signal?: AbortSignal
}

export interface UploadResult {
  /** 存储 key（相对路径） */
  key: string
  /** 可直接用于 img src / a href 的地址 */
  url: string
}

const DEFAULT_MAX_SIZE = 10 * 1024 * 1024

/** 同一策略的 STS 配置在页面生命周期内复用，避免每次上传都打一次接口 */
const stsCache = new Map<string, Promise<StsConfig>>()

/** 取上传策略配置（带内存缓存，失败自动失效） */
export function getStsConfig(policy: string): Promise<StsConfig> {
  const cached = stsCache.get(policy)
  if (cached) return cached

  const task = apiClient
    .get("/upload/sts", { params: { policy } })
    .then((res: unknown) => {
      const data = ((res as { data?: StsConfig }).data ?? res) as StsConfig
      // local 走同域上传（需要 cookie）；cos/oss 直传 bucket，不需要凭证
      if (data.provider === "local") {
        data.withCredentials = true
      }
      // 云存储时文件访问域名由后端下发，同步给 getFileUrl（本地存储回到构建时配置）
      setStorageBase(data.provider !== "local" ? data.url : null)
      return data
    })
    .catch((err) => {
      stsCache.delete(policy)
      throw err
    })

  stsCache.set(policy, task)
  return task
}

/** 取文件扩展名（小写，带点） */
export function extOf(name: string): string {
  const idx = name.lastIndexOf(".")
  return idx === -1 ? "" : name.slice(idx).toLowerCase()
}

/** 是否为图片文件 */
export function isImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) return true
  return [".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif", ".bmp"].includes(extOf(file.name))
}

/** 生成随机存储 key：prefix + 32 位随机串 + 原扩展名 */
function randomKey(prefix: string, file: File): string {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789"
  let str = ""
  for (let i = 0; i < 32; i++) str += chars[Math.floor(Math.random() * chars.length)]
  return prefix + str + (extOf(file.name) || ".bin")
}

/** 人性化文件大小文案，如 2MB / 512KB */
export function formatFileSize(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(0)}MB`
    : `${(bytes / 1024).toFixed(0)}KB`
}

/**
 * 走 STS 直传存储（local / COS / OSS 同一套表单协议）。
 *
 * 上传前按策略校验大小与扩展名，失败抛 Error（中文文案，可直接 toast）。
 */
export function uploadFile(file: File, options: UploadOptions = {}): Promise<UploadResult> {
  const { policy = "image", onProgress, signal } = options

  return getStsConfig(policy).then((config) => {
    const maxSize = config.max_file_size || DEFAULT_MAX_SIZE
    if (file.size > maxSize) {
      throw new Error(`文件大小不能超过 ${formatFileSize(maxSize)}`)
    }

    const ext = extOf(file.name)
    const allowed = (config.extensions || []).map((e) => e.toLowerCase())
    if (allowed.length && !allowed.includes("*") && ext && !allowed.includes(ext)) {
      throw new Error(`不支持的文件类型：${ext}`)
    }

    const key = randomKey(config.prefix, file)
    const formData = new FormData()
    Object.entries(config.payload || {}).forEach(([k, v]) => formData.append(k, String(v)))
    formData.append("key", key)
    formData.append("file", file)

    return new Promise<UploadResult>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.withCredentials = !!config.withCredentials
      xhr.open("POST", config.upload_url || config.url, true)

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress?.(Math.round((e.loaded / e.total) * 100))
      }

      xhr.onload = () => {
        if (xhr.status === config.success_action_status || xhr.status === 200) {
          resolve({ key, url: getFileUrl(key) })
        } else {
          reject(new Error(`上传失败（HTTP ${xhr.status}）`))
        }
      }
      xhr.onerror = () => reject(new Error("网络异常，上传失败"))
      xhr.onabort = () => reject(new Error("上传已取消"))

      signal?.addEventListener("abort", () => xhr.abort(), { once: true })

      xhr.send(formData)
    })
  })
}
