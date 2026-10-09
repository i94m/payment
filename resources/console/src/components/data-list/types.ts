import type { ReactNode } from "react"
import type { FieldValues } from "react-hook-form"
import type { ZodSchema } from "zod"

export type FormFieldType =
  | "input" | "textarea" | "number" | "password"
  | "select"
  | "switch"
  | "switch-group"
  | "date"
  | "editor"
  | "uploader" | "video"

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
  span?: "full" | "half"
  options?: SelectOption[]
  /** type=switch-group 时的开关项（一行展示，值为 boolean） */
  items?: SwitchGroupItem[]
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
