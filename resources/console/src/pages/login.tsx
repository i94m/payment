"use client"

import * as React from "react"
import { useAuthStore } from "@/stores/auth-store"
import { useSiteStore } from "@/stores/site-store"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"
import { IconUser, IconLock, IconAlertCircle, IconRefresh } from "@tabler/icons-react"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { useNavigate, useLocation, Link } from "react-router-dom"
import apiClient from "@/lib/api-client"
import { APP_MODULE } from "@/lib/config"

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const login = useAuthStore((s) => s.login)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const siteName = useSiteStore((s) => s.siteName)
  const siteFeatures = useSiteStore((s) => s.features)

  const [loading, setLoading] = React.useState(false)
  const [captchaKey, setCaptchaKey] = React.useState("")
  const [captchaImage, setCaptchaImage] = React.useState("")
  const [loginError, setLoginError] = React.useState("")
  const from = (location.state as { from?: string })?.from || "/"

  // 后端登录必须携带图片验证码（一次性，5 分钟有效）
  const isAgent = APP_MODULE === "agent"
  const schema = React.useMemo(() => z.object({
    username: z.string().min(1, isAgent ? "请输入代理名称" : "请输入用户名"),
    password: z.string().min(1, "请输入密码"),
    captchaCode: z.string().min(1, "请输入验证码"),
  }), [isAgent])

  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { username: "", password: "", captchaCode: "" },
  })

  const loadCaptcha = React.useCallback(async () => {
    try {
      const res = (await apiClient.get("/captcha")) as { key: string; content: string }
      setCaptchaKey(res.key)
      setCaptchaImage(res.content)
      form.setValue("captchaCode", "")
    } catch {
      // ignore
    }
  }, [form])

  React.useEffect(() => {
    loadCaptcha()
  }, [loadCaptcha])

  React.useEffect(() => {
    if (isAuthenticated) {
      navigate(from, { replace: true })
    }
  }, [isAuthenticated, navigate, from])

  if (isAuthenticated) {
    return null
  }

  const onSubmit = async (data: z.infer<typeof schema>) => {
    setLoading(true)
    setLoginError("")
    try {
      await login(data.username, data.password, captchaKey, data.captchaCode)
      navigate(from, { replace: true })
    } catch (err: unknown) {
      const res = err as { message?: string }
      loadCaptcha()
      setLoginError(res.message || "登录失败，请检查用户名和密码")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen bg-background">
      <div className="absolute top-1/2 left-1/2 w-80 -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-6 shadow-md sm:w-96">
        <h1 className="text-center text-2xl font-bold">{siteName}</h1>
        <p className="mt-1 text-center text-sm text-muted-foreground">
          {APP_MODULE === "merchant" ? "商户中心 · 输入您的账户信息以继续"
            : APP_MODULE === "agent" ? "代理中心 · 输入您的代理账号以继续"
              : "管理后台 · 输入您的账户信息以继续"}
        </p>

        <form onSubmit={form.handleSubmit(onSubmit)} className="mt-6 space-y-4">
          <Controller
            name="username"
            control={form.control}
            render={({ field, fieldState }) => (
              <Field>
                <FieldLabel htmlFor="username">{isAgent ? "代理名称" : "用户名"}<RequiredMark /></FieldLabel>
                <div className="relative">
                  <IconUser className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    {...field}
                    id="username"
                    placeholder={isAgent ? "请输入代理名称" : "请输入用户名"}
                    className="h-10 pl-9"
                    disabled={loading}
                    aria-invalid={fieldState.invalid}
                    onBlur={field.onBlur}
                    autoComplete="new-password"
                  />
                </div>
                {fieldState.invalid && (
                  <FieldError>{fieldState.error?.message}</FieldError>
                )}
              </Field>
            )}
          />

          <Controller
            name="password"
            control={form.control}
            render={({ field, fieldState }) => (
              <Field>
                <FieldLabel htmlFor="password">{"密码"}<RequiredMark /></FieldLabel>
                <div className="relative">
                  <IconLock className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    {...field}
                    id="password"
                    type="password"
                    placeholder={"请输入密码"}
                    className="h-10 pl-9"
                    disabled={loading}
                    aria-invalid={fieldState.invalid}
                    autoComplete="new-password"
                  />
                </div>
                {fieldState.invalid && (
                  <FieldError>{fieldState.error?.message}</FieldError>
                )}
              </Field>
            )}
          />

          {(
            <Field>
              <FieldLabel htmlFor="captchaCode">{"验证码"}<RequiredMark /></FieldLabel>
              <Controller
                name="captchaCode"
                control={form.control}
                render={({ field, fieldState }) => (
                  <>
                    <div className="flex items-center gap-2">
                      <Input
                        {...field}
                        id="captchaCode"
                        placeholder={"请输入验证码"}
                        className="h-10 flex-1"
                        disabled={loading}
                        autoComplete="off"
                      />
                      {captchaImage && (
                        <button
                          type="button"
                          onClick={loadCaptcha}
                          className="relative shrink-0"
                          title={"点击刷新验证码"}
                        >
                          <img
                            src={captchaImage}
                            alt="captcha"
                            className="h-10 w-[130px] cursor-pointer"
                          />
                        </button>
                      )}
                    </div>
                    {fieldState.invalid && (
                      <FieldError>{fieldState.error?.message}</FieldError>
                    )}
                  </>
                )}
              />
            </Field>
          )}

          {loginError && (
            <div className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <IconAlertCircle className="size-4 shrink-0" />
              <span>{loginError}</span>
            </div>
          )}

          <Button type="submit" className="h-10 w-full" disabled={loading}>
            {loading ? "登录中..." : "登录"}
          </Button>

          {APP_MODULE === "merchant" && siteFeatures.merchant_reg_open && (
            <div className="text-center text-sm text-muted-foreground">
              {"还没有账号？"}{" "}
              <Link to="/register" className="text-primary underline-offset-4 hover:underline">
                {"注册商户"}
              </Link>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
