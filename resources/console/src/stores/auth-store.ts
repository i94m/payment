import { create } from "zustand"
import apiClient from "@/lib/api-client"
import { STORAGE_PREFIX } from "@/lib/config"

export interface AuthUser {
  id: number
  username: string
  name: string
  email?: string
  phone?: string
  balance?: string
  is_super_admin: boolean
  permissions: string[]
}

interface AuthState {
  token: string
  user: AuthUser | null
  permissions: string[]
  isAuthenticated: boolean
  tokenExpiresAt: number
  login: (username: string, password: string, captchaKey?: string, captchaCode?: string) => Promise<void>
  /** SSO 登录：凭管理员下发的一次性 token 换取正式会话 */
  ssoLogin: (token: string) => Promise<void>
  /** 商户自助注册：成功即建立会话（仅商户端使用） */
  register: (payload: {
    username: string; password: string; email?: string;
    invite_code?: string; captcha_key: string; captcha_code: string
  }) => Promise<void>
  fetchProfile: () => Promise<void>
  logout: () => Promise<void>
  /** 清除当前会话(内存 store + localStorage),不调后端、不导航。供 401 拦截器使用。 */
  clearSession: () => void
}

const TOKEN_KEY = `${STORAGE_PREFIX}access-token`
const STATE_KEY = `${STORAGE_PREFIX}auth-state`
const EXPIRES_KEY = `${STORAGE_PREFIX}token-expires-at`

function getStoredToken(): string {
  if (typeof window === "undefined") return ""
  return localStorage.getItem(TOKEN_KEY) || ""
}

function getStoredState(): { user: AuthUser | null; permissions: string[] } {
  if (typeof window === "undefined") return { user: null, permissions: [] }
  try {
    const raw = localStorage.getItem(STATE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return { user: null, permissions: [] }
}

function getStoredExpiresAt(): number {
  if (typeof window === "undefined") return 0
  return Number(localStorage.getItem(EXPIRES_KEY)) || 0
}

function persistTokenAndExpiry(token: string, expiresIn: number) {
  const expiresAt = Date.now() + expiresIn * 1000
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(EXPIRES_KEY, String(expiresAt))
  return expiresAt
}

export const useAuthStore = create<AuthState>((set, get) => {
  let initialToken = getStoredToken()
  const initialExpiresAt = getStoredExpiresAt()
  let stored = getStoredState()

  // 本地记录的有效期已过：直接按未登录启动并清掉旧会话。
  // 否则会拿旧 token 先渲染后台框架、等接口 401 才弹回登录页，同样是「闪一下」。
  if (initialToken && initialExpiresAt > 0 && initialExpiresAt <= Date.now()) {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(STATE_KEY)
    localStorage.removeItem(EXPIRES_KEY)
    initialToken = ""
    stored = { user: null, permissions: [] }
  }

  const { user: initialUser, permissions: initialPermissions } = stored

  // 登录的公共收尾：持久化 token、置认证位、拉取 profile；
  // profile 失败则清空会话并抛出，交由调用方处理。
  async function establishSession(token: string, expiresIn: number) {
    const expiresAt = persistTokenAndExpiry(token, expiresIn)
    set({ token, isAuthenticated: true, tokenExpiresAt: expiresAt })
    try {
      await get().fetchProfile()
    } catch {
      get().clearSession()
      throw new Error("fetchProfile failed")
    }
  }

  return {
    token: initialToken,
    user: initialUser,
    permissions: initialPermissions,
    isAuthenticated: !!initialToken,
    tokenExpiresAt: getStoredExpiresAt(),

    login: async (username, password, captchaKey, captchaCode) => {
      const data = await apiClient.post("/auth/login", {
        username,
        password,
        captcha_key: captchaKey ?? "",
        captcha_code: captchaCode ?? "",
      }) as {
        access_token: string
        expires_in: number
        token_type: string
      }
      await establishSession(data.access_token, data.expires_in)
    },

    ssoLogin: async (token) => {
      const data = await apiClient.post("/auth/sso", { token }) as {
        access_token: string
        expires_in: number
        token_type: string
      }
      await establishSession(data.access_token, data.expires_in)
    },

    register: async (payload) => {
      const data = await apiClient.post("/auth/register", payload) as {
        access_token: string
        expires_in: number
        token_type: string
      }
      await establishSession(data.access_token, data.expires_in)
    },

    fetchProfile: async () => {
      const data = await apiClient.get("/auth/profile") as AuthUser
      const permissions = data.permissions || []
      const state = { user: data, permissions }
      localStorage.setItem(STATE_KEY, JSON.stringify(state))
      set(state)
    },

    logout: async () => {
      try {
        await apiClient.post("/auth/logout")
      } catch { /* ignore */ }
      get().clearSession()
      window.location.hash = "#/login"
    },

    clearSession: () => {
      localStorage.removeItem(TOKEN_KEY)
      localStorage.removeItem(STATE_KEY)
      localStorage.removeItem(EXPIRES_KEY)
      set({ token: "", user: null, permissions: [], isAuthenticated: false, tokenExpiresAt: 0 })
    },
  }
})
