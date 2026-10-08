"use client"

import * as React from "react"
import { useAuthStore } from "@/stores/auth-store"
import { useSiteStore } from "@/stores/site-store"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"
import { IconUser, IconLock, IconAlertCircle, IconMail, IconGift } from "@tabler/icons-react"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { useNavigate, Link } from "react-router-dom"
import apiClient from "@/lib/api-client"

/** 商户自助注册页：仅商户端入口挂载。 */
export function RegisterPage() {
  const navigate = useNavigate()
  const register = useAuthStore((s) => s.register)
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const siteName = useSiteStore((s) => s.siteName)
  const siteFeatures = useSiteStore((s) => s.features)

  const [loading, setLoading] = React.useState(false)
  const [captchaKey, setCaptchaKey] = React.useState("")
  const [captchaImage, setCaptchaImage] = React.useState("")
  const [regError, setRegError] = React.useState("")

  // 密码规则与后端一致：≥6 位、非纯数字、不得同用户名
  const schema = React.useMemo(() => z.object({
    username: z.string().min(1, "请输入用户名"),
    password: z.string().min(6, "密码至少 6 位").regex(/\D/, "密码不能为纯数字"),
    email: z.string().email("邮箱格式错误").optional().or(z.literal("")),
    inviteCode: z.string().optional(),
    captchaCode: z.string().min(1, "请输入验证码"),
  }), [])

  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { username: "", password: "", email: "", inviteCode: "", captchaCode: "" },
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
      navigate("/", { replace: true })
    }
  }, [isAuthenticated, navigate])

  if (isAuthenticated) {
    return null
  }

  // 注册开关未开放：不渲染表单（后端 /register 同样拒绝，双重防护）
  if (!siteFeatures.merchant_reg_open) {
    return (
      <div className="relative min-h-screen bg-background">
        <div className="absolute top-1/2 left-1/2 w-80 -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-6 shadow-md sm:w-96">
          <h1 className="text-center text-2xl font-bold">{siteName || "EPay"}</h1>
          <p className="mt-1 text-center text-sm text-muted-foreground">{"平台暂未开放商户注册，请联系管理员开通"}</p>
          <div className="mt-6 text-center text-sm">
            <Link to="/login" className="text-primary underline-offset-4 hover:underline">{"返回登录"}</Link>
          </div>
        </div>
      </div>
    )
  }

  const onSubmit = async (data: z.infer<typeof schema>) => {
    setLoading(true)
    setRegError("")
    try {
      await register({
        username: data.username,
        password: data.password,
        email: data.email || "",
        invite_code: data.inviteCode || "",
        captcha_key: captchaKey,
        captcha_code: data.captchaCode,
      })
      navigate("/", { replace: true })
    } catch (err: unknown) {
      const res = err as { message?: string }
      loadCaptcha()
      setRegError(res.message || "注册失败，请稍后重试")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen bg-background">
      <div className="absolute top-1/2 left-1/2 w-80 -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-6 shadow-md sm:w-96">
        <h1 className="text-center text-2xl font-bold">{siteName || "EPay"}</h1>
        <p className="mt-1 text-center text-sm text-muted-foreground">{"注册商户"}</p>

        <form onSubmit={form.handleSubmit(onSubmit)} className="mt-6 space-y-4">
          <Controller
            name="username"
            control={form.control}
            render={({ field, fieldState }) => (
              <Field>
                <FieldLabel htmlFor="username">{"用户名"}<RequiredMark /></FieldLabel>
                <div className="relative">
                  <IconUser className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    {...field}
                    id="username"
                    placeholder={"请输入用户名"}
                    className="h-10 pl-9"
                    disabled={loading}
                    aria-invalid={fieldState.invalid}
                  />
                </div>
                {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
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
                    placeholder={"至少 6 位，非纯数字"}
                    className="h-10 pl-9"
                    disabled={loading}
                    aria-invalid={fieldState.invalid}
                  />
                </div>
                {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
              </Field>
            )}
          />

          <Controller
            name="email"
            control={form.control}
            render={({ field, fieldState }) => (
              <Field>
                <FieldLabel htmlFor="email">{"邮箱（选填，用于找回密码）"}</FieldLabel>
                <div className="relative">
                  <IconMail className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    {...field}
                    id="email"
                    type="email"
                    placeholder="name@example.com"
                    className="h-10 pl-9"
                    disabled={loading}
                    aria-invalid={fieldState.invalid}
                  />
                </div>
                {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
              </Field>
            )}
          />

          <Controller
            name="inviteCode"
            control={form.control}
            render={({ field }) => (
              <Field>
                <FieldLabel htmlFor="inviteCode">{"邀请码（选填）"}</FieldLabel>
                <div className="relative">
                  <IconGift className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    {...field}
                    id="inviteCode"
                    placeholder={"有邀请码可填，无则留空"}
                    className="h-10 pl-9"
                    disabled={loading}
                  />
                </div>
              </Field>
            )}
          />

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
                          className="h-10 w-[130px] rounded-md border cursor-pointer"
                        />
                      </button>
                    )}
                  </div>
                  {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
                </>
              )}
            />
          </Field>

          {regError && (
            <div className="flex items-center gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              <IconAlertCircle className="size-4 shrink-0" />
              <span>{regError}</span>
            </div>
          )}

          <Button type="submit" className="h-10 w-full" disabled={loading}>
            {loading ? "注册中..." : "注册商户"}
          </Button>

          <div className="text-center text-sm text-muted-foreground">
            {"已有账号？"}{" "}
            <Link to="/login" className="text-primary underline-offset-4 hover:underline">
              {"登录"}
            </Link>
          </div>
        </form>
      </div>
    </div>
  )
}
