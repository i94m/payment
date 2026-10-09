"use client"

import * as React from "react"
import { accountApi } from "@/api"
import type { SettingItem } from "@/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Switch } from "@/components/ui/switch"
import { Field, FieldError, FieldLabel, RequiredMark } from "@/components/ui/field"
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { useForm, Controller, type Control } from "react-hook-form"
import { useSiteStore } from "@/stores/site-store"
import { zodResolver } from "@hookform/resolvers/zod"
import { z } from "zod"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "sonner"
import { IconRefresh } from "@tabler/icons-react"

// 设置项键由后端注册表动态下发，schema 放宽为字符串 KV；
// 数值/整数列表格式在提交时按 item.type 校验（见 buildPayload）
const settingsSchema = z.record(z.string(), z.string())

// 后端 group 值 → 分组标题（与 SettingGroup 对齐；未识别的组原样展示、排最后）
const GROUP_TITLES: Record<string, string> = {
  site: "站点",
  order: "扣量",
  notify: "通知",
  settle: "结算",
}
const GROUP_ORDER = Object.keys(GROUP_TITLES)

// 开关联动禁用：开关关闭（"false"）时禁用对应输入项
const TOGGLE_DISABLES: Record<string, string[]> = {
  deduct_enable: ["deduct_frequency", "deduct_minmoney"],
}

interface SettingGroup {
  key: string
  title: string
  /** 卡片标题下的补充描述（取组内第一个带 description 的项，可空） */
  description: string
  /** 开关类：整行渲染在输入项网格上方 */
  toggles: SettingItem[]
  /** 其余项：开关行下方的网格 */
  fields: SettingItem[]
}

/** 按后端下发的 group 聚合（组内保持下发顺序），按固定组序排列 */
function groupItems(items: SettingItem[]): SettingGroup[] {
  const map = new Map<string, SettingItem[]>()
  for (const item of items) {
    const list = map.get(item.group) ?? []
    list.push(item)
    map.set(item.group, list)
  }
  return [...map.entries()]
    .map(([key, groupItems]) => ({
      key,
      title: GROUP_TITLES[key] ?? key,
      description: groupItems.find((i) => i.description)?.description ?? "",
      toggles: groupItems.filter((i) => i.type === "bool"),
      fields: groupItems.filter((i) => i.type !== "bool"),
    }))
    .sort((a, b) => {
      const ia = GROUP_ORDER.indexOf(a.key)
      const ib = GROUP_ORDER.indexOf(b.key)
      return (ia < 0 ? GROUP_ORDER.length : ia) - (ib < 0 ? GROUP_ORDER.length : ib)
    })
}

const inputType = (type: string) =>
  type === "int" || type === "float" ? "number" : type === "decimal" ? "text" : "text"

// 数值/整数列表项留空会被提交校验拒绝，视为必填
const isRequiredType = (type: string) =>
  type === "int" || type === "float" || type === "decimal" || type === "list_int"

// 多行文本/列表类内容较长，整行占满；其余项一行多列自适应
const isFullRow = (type: string) =>
  type === "text" || type === "list" || type === "list_int"

/** 单个配置项字段（标签+输入框）；开关类不经此处 */
function SettingField({ item, control, clearError, disabled }: {
  item: SettingItem
  control: Control<Record<string, string>>
  clearError: () => void
  disabled?: boolean
}) {
  return (
    <div className={isFullRow(item.type) ? "sm:col-span-2 xl:col-span-3" : undefined}>
      <Controller
        name={item.key}
        control={control}
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
              disabled={disabled}
              value={field.value ?? ""}
              onChange={(e) => { field.onChange(e.target.value); clearError() }}
              placeholder={"请输入"}
              aria-invalid={fieldState.invalid || undefined}
            />
            {fieldState.error?.message && <FieldError>{fieldState.error.message}</FieldError>}
          </Field>
        )}
      />
    </div>
  )
}

export function PaySettingsPage() {
  const [items, setItems] = React.useState<SettingItem[]>([])
  const [loading, setLoading] = React.useState(true)
  // 分卡片保存：记录正在保存的组 key
  const [savingKey, setSavingKey] = React.useState<string | null>(null)
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

  const groups = React.useMemo(() => groupItems(items), [items])

  // 由开关联动禁用的键（watch 使组件随开关值重渲染）
  const disabledKeys = new Set<string>()
  for (const [toggle, keys] of Object.entries(TOGGLE_DISABLES)) {
    if (form.watch(toggle) === "false") keys.forEach((k) => disabledKeys.add(k))
  }

  const groupKeys = (group: SettingGroup) => [...group.toggles, ...group.fields]

  // 只校验/收集传入 scope 内的键（分卡片保存,PATCH 为部分更新）
  const buildPayload = (values: Record<string, string>, scope: SettingItem[]): Record<string, unknown> | null => {
    const payload: Record<string, unknown> = {}
    const errs: Record<string, string> = {}
    for (const item of scope) {
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

  // 单卡片保存：仅提交该组键；成功后不整页 load()（避免冲掉其他卡片未保存的编辑）
  const saveGroup = (group: SettingGroup) => {
    const payload = buildPayload(form.getValues(), groupKeys(group))
    if (!payload) return
    setSavingKey(group.key)
    accountApi.savePaySettings(payload)
      .then(() => {
        toast.success("设置已保存")
        // 站点名称等展示信息可能已变化，同步刷新（侧边栏/标签页标题）
        useSiteStore.getState().fetchSite()
      })
      .catch((err) => toast.error((err as { message?: string })?.message || "操作失败"))
      .finally(() => setSavingKey(null))
  }

  return (
    <div className="flex flex-1 flex-col">
      <div className="flex flex-1 flex-col gap-4 py-4 md:gap-6 md:py-6 w-full max-w-6xl">
        <div className="flex items-center justify-between px-4 lg:px-6">
          <div>
            <h2 className="text-lg font-semibold">{"站点配置"}</h2>
            <p className="text-xs text-muted-foreground mt-0.5">{"保存后立即生效，无需重启"}</p>
          </div>
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <IconRefresh className="size-4" />
            {"刷新"}
          </Button>
        </div>

        <div className="px-4 lg:px-6">
          <div className="space-y-6">
            {loading ? (
              // 加载骨架：两张分组卡片占位
              [0, 1].map((i) => (
                <Card key={i}>
                  <CardHeader>
                    <Skeleton className="h-5 w-24" />
                  </CardHeader>
                  <CardContent className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                    {Array.from({ length: 3 }).map((_, j) => (
                      <div key={j} className="space-y-2">
                        <Skeleton className="h-4 w-28" />
                        <Skeleton className="h-9 w-full" />
                      </div>
                    ))}
                  </CardContent>
                </Card>
              ))
            ) : (
              groups.map((group) => (
                <Card key={group.key}>
                  <CardHeader>
                    <CardTitle className="text-base">{group.title}</CardTitle>
                    {group.description && <CardDescription className="text-xs">{group.description}</CardDescription>}
                    <CardAction>
                      <Button
                        size="sm"
                        disabled={savingKey === group.key}
                        onClick={() => saveGroup(group)}
                      >
                        {savingKey === group.key ? "保存中…" : "保存"}
                      </Button>
                    </CardAction>
                  </CardHeader>
                  <CardContent className="grid gap-x-4 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
                    {group.toggles.length > 0 && (
                      // 开关整行置顶，横排模式：控件在前、标签在后
                      <div className="sm:col-span-2 xl:col-span-3 flex items-center gap-6">
                        {group.toggles.map((item) => (
                          <Controller
                            key={item.key}
                            name={item.key}
                            control={form.control}
                            render={({ field }) => (
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
                        ))}
                      </div>
                    )}
                    {group.fields.map((item) => (
                      <SettingField
                        key={item.key}
                        item={item}
                        control={form.control}
                        clearError={() => form.clearErrors(item.key)}
                        disabled={disabledKeys.has(item.key)}
                      />
                    ))}
                  </CardContent>
                </Card>
              ))
            )}
          </div>
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
