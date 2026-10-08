"use client"

import * as React from "react"
import { accountApi } from "@/api"
import type { SettingItem } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Field, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { useForm, Controller } from "react-hook-form"
import { useSiteStore } from "@/stores/site-store"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { IconRefresh } from "@tabler/icons-react"

// 设置项键由后端注册表动态下发，schema 放宽为字符串 KV；
// 数值/整数列表格式在提交时按 item.type 校验（见 buildPayload）
const settingsSchema = z.record(z.string(), z.string())

export function PaySettingsPage() {
  const [items, setItems] = React.useState<SettingItem[]>([])
  const [loading, setLoading] = React.useState(true)
  const [saving, setSaving] = React.useState(false)
  // 表单态统一存字符串(输入框友好),保存时按 item.type 转回注册表类型
  const form = useForm<Record<string, string>>({
    resolver: zodResolver(settingsSchema),
    defaultValues: {},
  })

  const load = React.useCallback(() => {
    setLoading(true)
    accountApi.paySettings()
      .then((res) => {
        setItems(res.items)
        form.reset(Object.fromEntries(res.items.map((i) => [i.key, toDisplay(i)])))
      })
      .catch(() => toast.error("加载失败"))
      .finally(() => setLoading(false))
  }, [form])

  React.useEffect(() => { load() }, [load])

  const buildPayload = (values: Record<string, string>): Record<string, unknown> | null => {
    const payload: Record<string, unknown> = {}
    const errs: Record<string, string> = {}
    for (const item of items) {
      const raw = (values[item.key] ?? "").trim()
      switch (item.type) {
        case "int":
        case "float": {
          const n = Number(raw)
          if (raw === "" || Number.isNaN(n)) {
            errs[item.key] = "请输入有效的数字"
            break
          }
          payload[item.key] = n
          break
        }
        case "decimal": {
          if (raw === "" || Number.isNaN(Number(raw))) {
            errs[item.key] = "请输入有效的数字"
            break
          }
          payload[item.key] = raw
          break
        }
        case "list_int": {
          const parts = raw.split(",").map((s) => s.trim()).filter(Boolean)
          const nums = parts.map(Number)
          if (parts.length === 0 || nums.some((n) => !Number.isInteger(n))) {
            errs[item.key] = "请输入逗号分隔的整数"
            break
          }
          payload[item.key] = nums
          break
        }
        case "bool":
          payload[item.key] = raw === "true"
          break
        default:
          payload[item.key] = raw
      }
    }
    if (Object.keys(errs).length > 0) {
      for (const [key, message] of Object.entries(errs)) {
        form.setError(key, { message })
      }
      return null
    }
    return payload
  }

  const save = form.handleSubmit(async (values) => {
    const payload = buildPayload(values)
    if (!payload) return
    setSaving(true)
    try {
      await accountApi.savePaySettings(payload)
      toast.success("设置已保存")
      // 站点名称等展示信息可能已变化，同步刷新（侧边栏/标签页标题）
      useSiteStore.getState().fetchSite()
      load()
    } catch (err) {
      toast.error((err as { message?: string })?.message || "操作失败")
    } finally {
      setSaving(false)
    }
  })

  const inputType = (type: string) =>
    type === "int" || type === "float" ? "number" : type === "decimal" ? "text" : "text"

  // 数值/整数列表项留空会被提交校验拒绝，视为必填
  const isRequiredType = (type: string) =>
    type === "int" || type === "float" || type === "decimal" || type === "list_int"

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-3xl">
        <div className="flex items-center justify-between px-4 lg:px-6">
          <div>
            <h2 className="text-lg font-semibold">{"支付设置"}</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{"保存后立即生效，无需重启"}</p>
          </div>
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <IconRefresh className="size-4" />
            {"刷新"}
          </Button>
        </div>

        <div className="px-4 lg:px-6">
          <form onSubmit={save}>
            <div className="space-y-4">
                {loading ? (
                  Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="space-y-2">
                      <Skeleton className="h-4 w-48" />
                      <Skeleton className="h-9 w-full" />
                    </div>
                  ))
                ) : (
                  items.map((item) => (
                    <div key={item.key} className="space-y-1.5">
                      {item.type === "bool" ? (
                        <Controller
                          name={item.key}
                          control={form.control}
                          render={({ field }) => (
                            // 开关：官方横排模式，控件在前、标签在后
                            <Field orientation="horizontal">
                              <Switch
                                id={`setting-${item.key}`}
                                checked={field.value === "true"}
                                onCheckedChange={(v) => field.onChange(String(v))}
                              />
                              <FieldLabel htmlFor={`setting-${item.key}`} className="font-normal text-sm">{item.label}</FieldLabel>
                            </Field>
                          )}
                        />
                      ) : (
                        <Controller
                          name={item.key}
                          control={form.control}
                          render={({ field, fieldState }) => (
                            <Field data-invalid={fieldState.invalid}>
                              <FieldLabel htmlFor={`setting-${item.key}`} className="text-sm">
                                {item.label}
                                {isRequiredType(item.type) && <RequiredMark />}
                              </FieldLabel>
                              <Input
                                id={`setting-${item.key}`}
                                type={inputType(item.type)}
                                step={item.type === "decimal" || item.type === "float" ? "0.01" : undefined}
                                value={field.value ?? ""}
                                onChange={(e) => { field.onChange(e.target.value); form.clearErrors(item.key) }}
                                placeholder={"请输入"}
                                aria-invalid={fieldState.invalid || undefined}
                              />
                              {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
                            </Field>
                          )}
                        />
                      )}
                    </div>
                  ))
                )}
            </div>

            {!loading && (
              <Field orientation="horizontal" className="mt-4">
                <Button type="submit" disabled={saving}>{"保存设置"}</Button>
              </Field>
            )}
          </form>
        </div>
      </div>
    </div>
  )
}

// ---- 显示/提交的类型转换(与后端注册表 SettingType 对应) ----

function toDisplay(item: SettingItem): string {
  const v = item.value
  if (item.type === "bool") return v === true || v === "true" ? "true" : "false"
  if (item.type === "list_int" || item.type === "list") {
    return Array.isArray(v) ? v.join(", ") : String(v ?? "")
  }
  return v === null || v === undefined ? "" : String(v)
}
