import type { ReactNode } from "react"
import type { FieldValues } from "react-hook-form"
import type { ZodSchema } from "zod"

export type FormFieldType =
  | "input" | "textarea" | "number" | "password"
  | "select"
  | "switch"
  | "checkbox"
  | "switch-group"
  | "checkbox-group"
  | "date"
  | "editor"
  | "uploader" | "video"
  | "icon-picker"

export interface SelectOption {
  label: string
  value: string | number
  disabled?: boolean
}

export interface SwitchGroupItem {
  name: string
  label: string
}

export interface FormFieldConfig {
  type: FormFieldType
  name: string
  label?: string
  /** 必填：Label 后追加红色 * */
  required?: boolean
  placeholder?: string
  /** 输入框单位（元 / % / 分钟…）：用 InputGroup 后缀展示，标签里不再写单位 */
  unit?: string
  span?: "full" | "half"
  options?: SelectOption[]
  /** type=switch-group / checkbox-group 时的选项（一行展示，值为 boolean） */
  items?: SwitchGroupItem[]
  /** type=checkbox-group 时把校验错误显示在整组下方（取该字段的错误信息） */
  errorField?: string
  props?: Record<string, unknown>
  dependsOn?: {
    field: string
    visible?: (value: unknown) => boolean
    disabled?: (value: unknown) => boolean
  }
}

export interface FormDialogConfig<T extends FieldValues = FieldValues> {
  schema: ZodSchema<T>
  fields: FormFieldConfig[]
  defaultValues: () => T
  onSubmit: (id: number | null, values: T) => Promise<void>
  fetchDetail?: (id: number) => Promise<Partial<T>>
  createTitle?: string
  editTitle?: string
  width?: string
}

export interface StatusBadgeOption {
  label: string
  value: string | number | boolean
  variant?: "default" | "secondary" | "destructive" | "outline"
}

export interface ActionItem<T> {
  label: string
  icon?: ReactNode
  variant?: "default" | "destructive"
  /** 悬停提示，缺省用 label */
  title?: string
  /** inline=平铺按钮（默认）；menu=收进「更多(⋮)」下拉子菜单 */
  placement?: "inline" | "menu"
  onClick: (row: T) => void
}
