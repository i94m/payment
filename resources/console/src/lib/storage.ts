import apiClient from "@/lib/api-client"

/** 构建时兜底：本地存储走同域相对路径，云存储可填 bucket 域名 */
const ENV_STORAGE_URL = (import.meta.env.VITE_STORAGE_URL || "/storage").replace(/\/+$/, "")

/**
 * 运行时访问前缀。
 *
 * 本地存储保持 `/storage`（交给 Vite 代理 / 后端 StaticFiles 同域服务）；
 * 切到 COS/OSS 后由后端 `/storage/info` 下发 bucket 域名，这里覆盖掉构建时的值，
 * 从而做到「只改后端 STORAGE_PROVIDER，前端跟着走」。
 */
let storageBase = ENV_STORAGE_URL

/** 设置文件访问前缀；传空值表示回到构建时配置 */
export function setStorageBase(host?: string | null): void {
  const next = (host || "").replace(/\/+$/, "")
  storageBase = next || ENV_STORAGE_URL
}

/** 当前生效的文件访问前缀 */
export function getStorageBase(): string {
  return storageBase
}

export function getFileUrl(path: string): string {
  if (!path) return ""
  if (path.startsWith("blob:") || path.startsWith("http")) return path
  return `${storageBase}/${path.replace(/^\/+/, "")}`
}

// 视频与图片走同一套存储地址拼装逻辑
export function getVideoUrl(path: string): string {
  return getFileUrl(path)
}

interface StorageInfo {
  provider?: string
  host?: string
}

let storageInfoTask: Promise<void> | null = null

/**
 * 拉取后端存储配置并同步访问前缀（登录后调用一次即可）。
 *
 * 失败时保持原有前缀（构建时 VITE_STORAGE_URL），不阻塞任何渲染。
 */
export function loadStorageConfig(): Promise<void> {
  if (storageInfoTask) return storageInfoTask

  storageInfoTask = apiClient
    .get("/storage/info")
    .then((res: unknown) => {
      const info = ((res as { data?: StorageInfo }).data ?? res) as StorageInfo
      // local 用相对路径（同域/代理），云存储用后端下发的 bucket 域名
      setStorageBase(info.provider && info.provider !== "local" ? info.host : null)
    })
    .catch(() => {
      storageInfoTask = null
    })

  return storageInfoTask
}
