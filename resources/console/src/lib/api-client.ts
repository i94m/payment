import axios from "axios"
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios"
import { STORAGE_PREFIX, BASE_URL } from "@/lib/config"

const TOKEN_KEY = `${STORAGE_PREFIX}access-token`

const apiClient = axios.create({
  baseURL: BASE_URL,
  timeout: Number(import.meta.env.VITE_API_TIMEOUT) || 60000,
  headers: {
    "Content-Type": "application/json",
    Accept: "application/json",
  },
})

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem(TOKEN_KEY)
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    // 二进制响应(CSV 导出等)直接透传，不走 JSON 包络解包
    if (!(response.data instanceof Blob)) {
      // 后端统一包络 {code, message, data}:code=0 视为成功，直接解出 data;
      // suc("文案") 时 data 为 null，需要提示文案的调用方改从响应头拿不到——
      // 这类接口调用方一律用通用成功 toast，故此处安全。
      const body = response.data as { code?: number; message?: string; data?: unknown }
      if (body && typeof body === "object" && "code" in body) {
        if (body.code === 0) return body.data as never
        return Promise.reject({ code: body.code, message: body.message })
      }
    }
    return response.data
  },
  (error) => {
    const status = error.response?.status
    const url: string = error.config?.url || ""

    // 登录态已失效(后端 token 过期/被撤销,但前端 store 仍认为有效):
    // 清除认证(内存 store + localStorage)并跳转登录页。
    // - 排除 /auth/login、/auth/sso:它们的 401 由各自页面处理,不该触发登出
    // - 以 store.isAuthenticated 去重:同一会话只清一次;重新登录后自动恢复 401 处理
    //   (避免并发 401 反复触发,也避免「登录页 ↔ 受保护页」来回弹的死循环)
    if (
      status === 401 &&
      !url.includes("/auth/login") &&
      !url.includes("/auth/sso")
    ) {
      // 惰性 import 规避 api-client ↔ auth-store 循环依赖
      void import("@/stores/auth-store").then(({ useAuthStore }) => {
        const store = useAuthStore.getState()
        if (!store.isAuthenticated) return
        store.clearSession()
        if (window.location.hash !== "#/login") {
          window.location.hash = "#/login"
        }
      })
    }

    // 403（无权限/权限被回收）：统一跳转 403 页，而不是只弹一个 toast。
    // 排除上传相关接口：上传策略/文件大小等业务性 403 仍由调用方提示。
    if (status === 403 && !url.startsWith("/upload")) {
      if (window.location.hash !== "#/403") {
        window.location.hash = "#/403"
      }
    }

    // 保留后端错误体(通常含 code/message，调用方可继续读 .message)，
    // 同时附带 HTTP status，便于调用方按状态码分支（如 403 / 429）。
    const data = error.response?.data
    const rejected =
      data && typeof data === "object"
        ? { ...(data as Record<string, unknown>), status }
        : { code: status ?? -1, message: error.message, status }
    return Promise.reject(rejected)
  }
)

export default apiClient
