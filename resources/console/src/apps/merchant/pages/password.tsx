"use client"

import * as React from "react"
import { authApi } from "@/api/auth"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { useAuthStore } from "@/stores/auth-store"
import { IconLock } from "@tabler/icons-react"
import { getErrorMessage } from "@/lib/error"
import { toast } from "sonner"

// 修改密码：与后端口径一致，至少 6 位且非纯数字
const changePasswordSchema = z
  .object({
    oldPassword: z.string().min(1, "原密码不能为空"),
    newPassword: z.string().min(1, "新密码不能为空"),
    confirmPassword: z.string().min(1, "确认新密码不能为空"),
  })
  .superRefine((v, ctx) => {
    if (v.newPassword.trim().length < 6) {
      ctx.addIssue({ code: "custom", path: ["newPassword"], message: "新密码至少 6 位" })
    } else if (!/\D/.test(v.newPassword.trim())) {
      ctx.addIssue({ code: "custom", path: ["newPassword"], message: "新密码不能为纯数字" })
    }
    if (v.confirmPassword !== v.newPassword) {
      ctx.addIssue({ code: "custom", path: ["confirmPassword"], message: "两次输入的新密码不一致" })
    }
  })

export function PasswordPage() {
  const logout = useAuthStore((s) => s.logout)
  const [saving, setSaving] = React.useState(false)

  const form = useForm<z.infer<typeof changePasswordSchema>>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { oldPassword: "", newPassword: "", confirmPassword: "" },
  })

  const submit = form.handleSubmit(async (values) => {
    setSaving(true)
    try {
      await authApi.changePassword(values.oldPassword, values.newPassword)
      toast.success("密码修改成功，请重新登录")
      form.reset()
      setTimeout(() => logout(), 1500)
    } catch (err) {
      toast.error(getErrorMessage(err, "操作失败"))
    } finally {
      setSaving(false)
    }
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-xl">
        <div className="px-4 lg:px-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <IconLock className="size-4 text-primary" />
            {"修改密码"}
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">{"修改成功后需重新登录"}</p>
        </div>
        <div className="px-4 lg:px-6">
          <form onSubmit={submit} className="grid gap-3">
              <Controller
                name="oldPassword"
                control={form.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"原密码"}<RequiredMark /></FieldLabel>
                    <Input id={field.name} type="password" className="w-full sm:max-w-sm" value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={"请输入原密码"} autoComplete="new-password" aria-invalid={fieldState.invalid || undefined} />
                    {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="newPassword"
                control={form.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"新密码"}<RequiredMark /></FieldLabel>
                    <Input id={field.name} type="password" className="w-full sm:max-w-sm" value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={"至少 6 位，非纯数字"} autoComplete="new-password" aria-invalid={fieldState.invalid || undefined} />
                    {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="confirmPassword"
                control={form.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"确认新密码"}<RequiredMark /></FieldLabel>
                    <Input id={field.name} type="password" className="w-full sm:max-w-sm" value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={"请再次输入新密码"} autoComplete="new-password" aria-invalid={fieldState.invalid || undefined} />
                    {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Field orientation="horizontal">
                <Button type="submit" disabled={saving}>{"修改密码"}</Button>
              </Field>
            </form>
        </div>
      </div>
    </div>
  )
}
