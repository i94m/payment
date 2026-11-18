import type { Control, FieldValues, Path } from "react-hook-form"
import * as React from "react"
import { Controller } from "react-hook-form"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { Switch } from "@/components/ui/switch"
import { Uploader } from "@/components/ui/uploader"
import { DatePicker } from "@/components/ui/date-picker"
import { Field, FieldLabel, FieldError, RequiredMark } from "@/components/ui/field"

// RichEditor(tiptap + prosemirror) 体积大，lazy 加载：仅当表单真正用到 editor 字段时才拉取
const RichEditor = React.lazy(() =>
  import("@/components/editor/rich-editor").then((m) => ({ default: m.RichEditor })),
)
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { FormFieldConfig } from "./types"

interface FormFieldRendererProps<T extends FieldValues> {
  config: FormFieldConfig
  control: Control<T>
  formData: Record<string, unknown>
  disabled?: boolean
}

export function FormFieldRenderer<T extends FieldValues>({
  config,
  control,
  formData,
  disabled,
}: FormFieldRendererProps<T>) {
  const fieldName = config.name as Path<T>

  // 开关组：一行多个开关，每项独立注册到表单（值为 boolean）
  if (config.type === "switch-group") {
    return (
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {config.items?.map((item) => (
          <Controller
            key={item.name}
            name={item.name as Path<T>}
            control={control}
            render={({ field }) => (
              <label
                htmlFor={item.name}
                className="flex cursor-pointer items-center gap-2"
              >
                <Switch
                  id={item.name}
                  checked={!!field.value}
                  onCheckedChange={field.onChange}
                  disabled={disabled}
                />
                <span className="text-sm text-muted-foreground">{item.label}</span>
              </label>
            )}
          />
        ))}
      </div>
    )
  }

  if (config.dependsOn) {
    const depValue = formData[config.dependsOn.field]
    if (config.dependsOn.visible && !config.dependsOn.visible(depValue)) {
      return null
    }
  }

  return (
    <Controller
      name={fieldName}
      control={control}
      render={({ field, fieldState }) => (
        <Field data-invalid={fieldState.invalid}>
          {config.type !== "switch" && config.label && (
            <FieldLabel htmlFor={fieldName}>
              {config.label}
              {config.required && <RequiredMark />}
            </FieldLabel>
          )}
          {renderField(config, field, disabled)}
          {config.type === "switch" && config.label && (
            <span className="text-sm text-muted-foreground ml-2">{config.label}</span>
          )}
          {fieldState.invalid && fieldState.error?.message && (
            <FieldError>{fieldState.error.message}</FieldError>
          )}
        </Field>
      )}
    />
  )
}

function renderField(
  config: FormFieldConfig,
  field: { value: unknown; onChange: (value: unknown) => void; onBlur: () => void },
  disabled?: boolean,
) {
  switch (config.type) {
    case "input":
    case "password":
      return (
        <Input
          type={config.type === "password" ? "password" : "text"}
          id={config.name}
          placeholder={config.placeholder}
          value={(field.value as string) ?? ""}
          onChange={(e) => field.onChange(e.target.value)}
          onBlur={field.onBlur}
          disabled={disabled}
          // 密码类字段一律不参与浏览器自动填充（后台新增/编辑表单）
          {...(config.type === "password" ? { autoComplete: "new-password" } : {})}
          {...(config.props as Record<string, unknown>)}
        />
      )

    case "number":
      return (
        <Input
          type="number"
          id={config.name}
          placeholder={config.placeholder}
          value={(field.value as string) ?? ""}
          onChange={(e) => field.onChange(e.target.value ? Number(e.target.value) : "")}
          onBlur={field.onBlur}
          disabled={disabled}
          {...(config.props as Record<string, unknown>)}
        />
      )

    case "textarea":
      return (
        <Textarea
          id={config.name}
          placeholder={config.placeholder}
          value={(field.value as string) ?? ""}
          onChange={(e) => field.onChange(e.target.value)}
          onBlur={field.onBlur}
          disabled={disabled}
          {...(config.props as Record<string, unknown>)}
        />
      )

    case "select":
      return (
        <Select
          value={String(field.value ?? "")}
          onValueChange={field.onChange}
        >
          <SelectTrigger id={config.name} className="w-full" disabled={disabled}>
            <SelectValue placeholder={config.placeholder} />
          </SelectTrigger>
          <SelectContent>
            {config.options?.map((opt) => (
              <SelectItem
                key={String(opt.value)}
                value={String(opt.value)}
                disabled={opt.disabled}
              >
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )

    case "date": {
      const dateValue = field.value ? new Date(field.value as string) : undefined
      return (
        <DatePicker
          value={dateValue}
          onChange={(d) => field.onChange(d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : "")}
          placeholder={config.placeholder}
          disabled={disabled}
        />
      )
    }

    case "switch":
      return (
        <div className="flex items-center gap-2 pt-1">
          <Switch
            checked={!!field.value}
            onCheckedChange={field.onChange}
            disabled={disabled}
          />
        </div>
      )

    case "editor":
      return (
        <React.Suspense fallback={<div className="h-32 animate-pulse rounded-md bg-muted" />}>
          <RichEditor
            value={(field.value as string) ?? ""}
            onChange={field.onChange}
          />
        </React.Suspense>
      )

    case "uploader":
      return (
        <Uploader
          value={field.value as string | string[] | undefined}
          onChange={field.onChange}
          disabled={disabled}
          {...(config.props as Record<string, unknown>)}
        />
      )

    case "video":
      return (
        <Uploader
          value={field.value as string | string[] | undefined}
          onChange={field.onChange}
          policy="video"
          disabled={disabled}
          {...(config.props as Record<string, unknown>)}
        />
      )

    default:
      return null
  }
}
