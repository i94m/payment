import { Fragment, type ReactNode } from "react"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

export interface FilterRadioOption {
  label: string
  value: string
}

export interface FilterFieldConfig {
  type: "radio" | "select" | "date" | "dateRange" | "input" | "custom"
  field: string
  label?: string
  placeholder?: string
  options?: FilterRadioOption[]
  render?: ReactNode
}

export interface FilterBarProps {
  filters: Record<string, unknown>
  onChange: (field: string, value: unknown) => void
  fields: FilterFieldConfig[]
  children?: ReactNode
}

export function FilterBar({ filters, onChange, fields, children }: FilterBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      {fields.map((field) => {
        switch (field.type) {
          case "radio":
            return (
              <ToggleGroup
                key={field.field}
                type="single"
                variant="outline"
                size="sm"
                value={(filters[field.field] as string) || ""}
                onValueChange={(val) => {
                  if (val) onChange(field.field, val)
                }}
              >
                {field.options?.map((opt) => (
                  <ToggleGroupItem key={opt.value} value={opt.value || "_all"}>
                    {opt.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            )
          case "custom":
            return <Fragment key={field.field}>{field.render}</Fragment>
          default:
            return null
        }
      })}
      {children}
    </div>
  )
}
