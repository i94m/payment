import apiClient from "@/lib/api-client"
import { APP_MODULE } from "@/lib/config"

export interface LoginResult {
  token_type: string
  access_token: string
  expires_in: number
}

export interface CaptchaData {
  content: string // data URL
  key: string
}

export interface SessionToken {
  id: number
  name: string
  ip: string
  device: string
  browser: string
  last_used_at: string | null
  created_at: string | null
  is_current: boolean
}

export const authApi = {
  /** 图片验证码（admin/merchant 同构，各自前缀下） */
  captcha: () => apiClient.get("/captcha") as Promise<CaptchaData>,

  login: (payload: { username: string; password: string; captcha_key: string; captcha_code: string }) =>
    apiClient.post("/auth/login", payload) as Promise<LoginResult>,

  logout: () => apiClient.post("/auth/logout"),

  /** 登录设备列表（双端同构） */
  sessions: () => apiClient.get("/auth/tokens") as Promise<SessionToken[]>,

  revokeSession: (id: number) => apiClient.delete(`/auth/tokens/${id}`),

  /** 修改密码：admin → /common/changePassword，merchant → /account/changePassword */
  changePassword: (oldPassword: string, newPassword: string) =>
    APP_MODULE === "merchant"
      ? apiClient.post("/account/changePassword", { old_password: oldPassword, new_password: newPassword })
      : apiClient.post("/changePassword", { old_password: oldPassword, new_password: newPassword }),

  /** 商户端才有资料编辑接口；admin 端返回 rejected，调用方按模块隐藏入口 */
  updateProfile: (data: Record<string, unknown>) => {
    if (APP_MODULE !== "merchant") {
      return Promise.reject({ code: -1, message: "管理员资料由超级管理员在账号管理中维护" })
    }
    return apiClient.put("/account/profile", data)
  },
}
