"use client"

import * as React from "react"
import { useAuthStore } from "@/stores/auth-store"
import { authApi } from "@/api/auth"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Field, FieldLabel, FieldError, FieldGroup, RequiredMark } from "@/components/ui/field"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { toast } from "sonner"
import apiClient from "@/lib/api-client"
import { APP_MODULE } from "@/lib/config"
import { IconLock } from "@tabler/icons-react"

export function UserCenterPage() {
  // 商户端：直接展示可编辑的资料表单（改密走独立的 /password 页面）；
  // admin 端资料由超级管理员在账号管理中维护，仅保留只读查看 + 改密弹窗。
  if (APP_MODULE === "merchant") {
    return <MerchantProfileCard />
  }
  return <AdminAccountCard />
}

// ============================================================
// 商户端：修改资料（收款账号设置，对齐老系统 editinfo.php）
// ============================================================

/** 结算方式选项：accountLabel 为「收款账号」字段的动态标签 */
const SETTLE_TYPE_OPTIONS = [
  { value: 1, label: "支付宝", accountLabel: "支付宝账号" },
  { value: 2, label: "微信", accountLabel: "微信号" },
  { value: 3, label: "QQ钱包", accountLabel: "ＱＱ号码" },
  { value: 4, label: "银行卡", accountLabel: "银行卡号" },
]

const settleSchema = z.object({
  settleType: z.number(),
  settleAccount: z.string().min(1, "请输入收款账号"),
  settleName: z.string().min(1, "请输入真实姓名"),
})

function MerchantProfileCard() {
  const [saving, setSaving] = React.useState(false)

  const settleForm = useForm<z.infer<typeof settleSchema>>({
    resolver: zodResolver(settleSchema),
    defaultValues: {
      settleType: 1, settleAccount: "", settleName: "",
    },
  })

  // 收款账号回显；接口为部分更新语义，未提交的资料字段不会被清空
  React.useEffect(() => {
    apiClient.get("/auth/profile").then((p) => {
      const profile = p as unknown as Record<string, never>
      settleForm.reset({
        settleType: Number(profile.settle_type) || 1,
        settleAccount: profile.settle_account || "",
        settleName: profile.settle_name || "",
      })
    }).catch(() => {})
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 结算方式切换时同步「收款账号」字段标签
  const settleType = settleForm.watch("settleType")
  const accountLabel = SETTLE_TYPE_OPTIONS.find((o) => o.value === settleType)?.accountLabel || "收款账号"

  const saveSettle = settleForm.handleSubmit(async (values) => {
    setSaving(true)
    try {
      await authApi.updateProfile({
        settle_type: values.settleType,
        settle_account: values.settleAccount,
        settle_name: values.settleName,
      })
      toast.success("更新成功")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setSaving(false)
    }
  })

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-lg">
        <div className="px-4 lg:px-6">
          <form onSubmit={saveSettle} className="space-y-3">
              <p className="text-sm font-medium">{"收款账号设置"}</p>
              <Controller
                name="settleType"
                control={settleForm.control}
                render={({ field }) => (
                  <Field>
                    <FieldLabel htmlFor="settle_type">{"结算方式"}</FieldLabel>
                    <Select
                      value={field.value !== undefined ? String(field.value) : undefined}
                      onValueChange={(v) => field.onChange(Number(v))}
                    >
                      <SelectTrigger id="settle_type" className="w-full">
                        <SelectValue placeholder="请选择结算方式" />
                      </SelectTrigger>
                      <SelectContent>
                        {SETTLE_TYPE_OPTIONS.map((o) => (
                          <SelectItem key={o.value} value={String(o.value)}>{o.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                )}
              />
              <Controller
                name="settleAccount"
                control={settleForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{accountLabel}<RequiredMark /></FieldLabel>
                    <Input id={field.name} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={`请输入${accountLabel}`} />
                    {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="settleName"
                control={settleForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"真实姓名"}<RequiredMark /></FieldLabel>
                    <Input id={field.name} value={field.value ?? ""} onChange={(e) => field.onChange(e.target.value)} placeholder={"请输入真实姓名"} />
                    {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                  </Field>
                )}
              />
              <Field orientation="horizontal">
                <Button type="submit" disabled={saving}>{"确定修改"}</Button>
              </Field>
            </form>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// admin 端：账号信息（只读）+ 修改密码弹窗
// ============================================================

function AdminAccountCard() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const [loading, setLoading] = React.useState(false)
  const [passwordOpen, setPasswordOpen] = React.useState(false)

  const name = user?.name || "User"
  const email = user?.email || user?.username || ""
  const initial = name.charAt(0).toUpperCase()

  const passwordSchema = z.object({
    old_password: z.string().min(1, "请输入原密码"),
    password: z.string().min(6, "密码至少为6位"),
    re_password: z.string().min(1, "请再次输入密码"),
  }).refine((data) => data.password !== data.old_password, {
    message: "新密码不能和原密码相同",
    path: ["password"],
  }).refine((data) => data.password === data.re_password, {
    message: "两次密码输入不一致",
    path: ["re_password"],
  })

  const passwordForm = useForm<z.infer<typeof passwordSchema>>({
    resolver: zodResolver(passwordSchema),
    defaultValues: { old_password: "", password: "", re_password: "" },
  })

  const onPasswordSubmit = async (values: z.infer<typeof passwordSchema>) => {
    setLoading(true)
    try {
      await authApi.changePassword(values.old_password, values.password)
      toast.success("密码更改成功，请重新登录")
      setPasswordOpen(false)
      setTimeout(() => logout(), 1500)
    } catch (err: unknown) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setLoading(false)
    }
  }

  const fields: { label: string; value: React.ReactNode }[] = [
    { label: "登录账号", value: user?.username || "-" },
    { label: "姓名", value: user?.name || "-" },
    { label: "邮箱", value: user?.email || "-" },
    {
      label: "账号角色",
      value: (
        <Badge variant="secondary" className="text-xs">
          {user?.is_super_admin ? "超级管理员" : "-"}
        </Badge>
      ),
    },
  ]

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-lg">
        <div className="flex items-start justify-between px-4 lg:px-6">
          <div>
            <h2 className="text-lg font-semibold">{"基本信息"}</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{"查看和管理您的个人资料"}</p>
          </div>
          <Button variant="outline" size="sm" onClick={() => { setPasswordOpen(true); passwordForm.reset() }}>
            <IconLock className="size-4" />
            {"修改密码"}
          </Button>
        </div>
        <div className="flex flex-col gap-6 px-4 lg:px-6">
          <div className="flex items-center gap-4">
            <Avatar className="h-16 w-16 rounded-full border-2 border-border">
              <AvatarFallback className="rounded-full bg-primary/10 text-primary text-xl">{initial}</AvatarFallback>
            </Avatar>
            <div>
              <p className="text-base font-medium">{name}</p>
              <p className="text-sm text-muted-foreground">{email}</p>
            </div>
          </div>
          <div className="divide-y">
            {fields.map((field) => (
              <div key={field.label} className="flex items-center justify-between py-4 first:pt-0 last:pb-0">
                <span className="text-sm text-muted-foreground">{field.label}</span>
                <span className="text-sm font-medium">{field.value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Change Password Modal */}
      <Dialog open={passwordOpen} onOpenChange={setPasswordOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"修改密码"}</DialogTitle>
          </DialogHeader>
          <form onSubmit={passwordForm.handleSubmit(onPasswordSubmit)}>
            <FieldGroup>
              <Controller
                name="old_password"
                control={passwordForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"原密码"}<RequiredMark /></FieldLabel>
                    <Input {...field} id={field.name} type="password" placeholder={"输入原密码"} />
                    {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="password"
                control={passwordForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"新密码"}<RequiredMark /></FieldLabel>
                    <Input {...field} id={field.name} type="password" placeholder={"设置新密码"} />
                    {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
                  </Field>
                )}
              />
              <Controller
                name="re_password"
                control={passwordForm.control}
                render={({ field, fieldState }) => (
                  <Field data-invalid={fieldState.invalid}>
                    <FieldLabel htmlFor={field.name}>{"确认密码"}<RequiredMark /></FieldLabel>
                    <Input {...field} id={field.name} type="password" placeholder={"确认新密码"} />
                    {fieldState.invalid && <FieldError>{fieldState.error?.message}</FieldError>}
                  </Field>
                )}
              />
            </FieldGroup>
            <DialogFooter className="mt-6">
              <Button variant="outline" type="button" onClick={() => setPasswordOpen(false)}>
                {"取消"}
              </Button>
              <Button type="submit" disabled={loading}>
                {loading ? "加载中..." : "确认修改"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
