const STORAGE_URL = import.meta.env.VITE_STORAGE_URL || "/storage"

export function getFileUrl(path: string): string {
  if (!path) return ""
  if (path.startsWith("blob:") || path.startsWith("http")) return path
  return `${STORAGE_URL}/${path}`
}

// 视频与图片走同一套存储地址拼装逻辑
export function getVideoUrl(path: string): string {
  return getFileUrl(path)
}
