"use client"

import * as React from "react"
import { paypageApi } from "@/api"
import type { PaypageSettings } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { CopyableInput } from "@/components/ui/copy-button"
import { Field, FieldDescription, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { useForm, Controller } from "react-hook-form"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { numStr } from "@/lib/form-schema"
import { toast } from "sonner"

// 收款页设置：随机金额开启时最小/最大金额必填且仅支持整数；建单限流为非负整数（0=不限）
const paypageSchema = z.object({
  randomEnable: z.boolean(),
  randomMin: numStr("最小金额", { min: 0.01, integer: true, optional: true }),
  randomMax: numStr("最大金额", { min: 0.01, integer: true, optional: true }),
  ratelimit: z.number().int("建单限流需为整数").min(0, "建单限流不能小于 0"),
}).superRefine((v, ctx) => {
  if (!v.randomEnable) return
  if (!v.randomMin.trim()) ctx.addIssue({ code: "custom", path: ["randomMin"], message: "最小金额不能为空" })
  if (!v.randomMax.trim()) ctx.addIssue({ code: "custom", path: ["randomMax"], message: "最大金额不能为空" })
})

export function PaypageMchPage() {
  const [settings, setSettings] = React.useState<PaypageSettings | null>(null)
  const [saving, setSaving] = React.useState(false)

  const form = useForm<z.infer<typeof paypageSchema>>({
    resolver: zodResolver(paypageSchema),
    defaultValues: { randomEnable: false, randomMin: "0", randomMax: "0", ratelimit: 0 },
  })
  // 随机金额开关：控制金额输入禁用态与必填标记（watch 订阅以驱动条件渲染）
  const randomEnable = form.watch("randomEnable")

  React.useEffect(() => {
    paypageApi.settings().then((res) => {
      setSettings(res)
      form.reset({
        randomEnable: res.random_enable,
        // 数据库存 Numeric(12,2)，回显去掉 .00 尾零（5.00 → 5）
        randomMin: String(Number(res.random_min)),
        randomMax: String(Number(res.random_max)),
        ratelimit: res.random_ratelimit,
      })
    }).catch(() => {})
  }, [form])

  const save = form.handleSubmit(async (values) => {
    // 随机金额开启时校验区间（原 setErrors 手工字段错误 → form.setError）
    if (values.randomEnable && Number(values.randomMin) > Number(values.randomMax)) {
      form.setError("randomMax", { message: "最大金额不能小于最小金额" })
      return
    }
    setSaving(true)
    try {
      await paypageApi.update({
        random_enable: values.randomEnable, random_min: values.randomMin,
        random_max: values.randomMax, random_ratelimit: Number(values.ratelimit) || 0,
      })
      toast.success("保存成功")
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setSaving(false)
    }
  })

  const link = settings
    ? (typeof window !== "undefined" ? window.location.origin : "") + settings.link
    : ""

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-3xl">
        <div className="px-4 lg:px-6">
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            {"聚合收款"}
            {settings && (
              <Badge variant={settings.enable ? "default" : "secondary"}>
                {settings.enable ? "已开放" : "平台未开放"}
              </Badge>
            )}
          </h2>
          <p className="text-xs text-muted-foreground mt-0.5">{"免登录收款页：付款人打开链接输入金额即可付款，支持代理归属"}</p>
        </div>
        <div className="px-4 lg:px-6 space-y-4">
          <Field>
            <FieldLabel htmlFor="pay-link">{"收款链接"}</FieldLabel>
            <CopyableInput id="pay-link" value={link} className="w-full font-mono text-xs" />
          </Field>

              <form onSubmit={save} className="space-y-3 rounded-lg border p-3">
                <Controller
                  name="randomEnable"
                  control={form.control}
                  render={({ field }) => (
                    <Field orientation="horizontal">
                      <input id={field.name} type="checkbox" checked={!!field.value}
                        onChange={(e) => field.onChange(e.target.checked)} />
                      <FieldLabel htmlFor={field.name} className="font-normal">
                        {"随机金额模式（金额由服务端在区间内生成）"}
                      </FieldLabel>
                    </Field>
                  )}
                />
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <Controller
                    name="randomMin"
                    control={form.control}
                    render={({ field, fieldState }) => (
                      <Field data-invalid={fieldState.invalid}>
                        <FieldLabel htmlFor={field.name}>{"最小金额"}{randomEnable && <RequiredMark />}</FieldLabel>
                        <Input id={field.name} inputMode="numeric" value={field.value ?? ""} placeholder={"如 1"}
                          onChange={(e) => field.onChange(e.target.value)} disabled={!randomEnable}
                          aria-invalid={fieldState.invalid || undefined} />
                        {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                      </Field>
                    )}
                  />
                  <Controller
                    name="randomMax"
                    control={form.control}
                    render={({ field, fieldState }) => (
                      <Field data-invalid={fieldState.invalid}>
                        <FieldLabel htmlFor={field.name}>{"最大金额"}{randomEnable && <RequiredMark />}</FieldLabel>
                        <Input id={field.name} inputMode="numeric" value={field.value ?? ""} placeholder={"如 100"}
                          onChange={(e) => field.onChange(e.target.value)} disabled={!randomEnable}
                          aria-invalid={fieldState.invalid || undefined} />
                        {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                      </Field>
                    )}
                  />
                  <Controller
                    name="ratelimit"
                    control={form.control}
                    render={({ field, fieldState }) => (
                      <Field data-invalid={fieldState.invalid}>
                        <FieldLabel htmlFor={field.name}>{"建单限流/分钟"}</FieldLabel>
                        <Input id={field.name} inputMode="numeric" value={field.value} placeholder={"如 10，0=不限"}
                          onChange={(e) => field.onChange(Number(e.target.value) || 0)}
                          aria-invalid={fieldState.invalid || undefined} />
                        {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                      </Field>
                    )}
                  />
                </div>
                <FieldDescription className="text-xs">{"同一付款 IP 每分钟对该商户的建单上限，0=不限"}</FieldDescription>
                <Field orientation="horizontal">
                  <Button type="submit" disabled={saving || !settings?.enable}>{"保存设置"}</Button>
                </Field>
              </form>
        </div>
      </div>
    </div>
  )
}
