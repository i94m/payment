"use client"

/**
 * SSO 落地页（商户端/代理端）：管理员在后台「SSO 登录」或商户在代理管理页「登录」发起，
 * 携带一次性 token 跳转到此页，用 token 换取正式 JWT 后进入对应后台。
 * 凭证 60 秒有效且一次性，失败可返回登录页。
 */
import * as React from "react"
import { useNavigate, useSearchParams } from "react-router-dom"
import { useAuthStore } from "@/stores/auth-store"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { toast } from "sonner"
import { APP_MODULE } from "@/lib/config"

export function SsoLoginPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const ssoLogin = useAuthStore((s) => s.ssoLogin)
  const isAgent = APP_MODULE === "agent"
  const expiredTip = isAgent ? "登录凭证无效或已过期，请让商户重新发起" : "登录凭证无效或已过期，请让管理员重新发起"
  // 一次性凭证：防 StrictMode(开发)下 effect 双执行把 token 消耗掉
  const startedRef = React.useRef(false)

  React.useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    const token = searchParams.get("token") || ""
    if (!token) {
      toast.error(expiredTip)
      navigate("/login", { replace: true })
      return
    }
    ssoLogin(token)
      .then(() => {
        toast.success("登录成功")
        navigate("/", { replace: true })
      })
      .catch((err: { message?: string }) => {
        toast.error(err?.message || expiredTip)
        navigate("/login", { replace: true })
      })
    // 仅首次挂载执行一次，避免 StrictMode 双发换 token 请求（一次性凭证第二次必失败）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="flex min-h-svh flex-col items-center justify-center gap-3">
      <Spinner className="size-6" />
      <p className="text-sm text-muted-foreground">{isAgent ? "正在登录代理面板…" : "正在登录商户后台…"}</p>
      <Button variant="outline" size="sm" onClick={() => navigate("/login", { replace: true })}>
        {"登录"}
      </Button>
    </div>
  )
}
