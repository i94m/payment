"use client"

import * as React from "react"
import { cn } from "@/lib/utils"
import apiClient from "@/lib/api-client"
import { Cascader } from "@/components/ui/cascader"
import type { CascaderOption } from "@/components/ui/cascader"

interface RegionSelectProps {
  value?: string[]
  onChange?: (value: string[]) => void
  placeholder?: string
  level?: 1 | 2 | 3
  className?: string
  disabled?: boolean
}

function RegionSelect({
  value = [],
  onChange,
  placeholder = "请选择地区",
  level = 3,
  className,
  disabled,
}: RegionSelectProps) {
  const [options, setOptions] = React.useState<CascaderOption[]>([])

  React.useEffect(() => {
    apiClient
      .get("/data/region/tree", { params: { level } })
      .then((res: any) => {
        const data = Array.isArray(res) ? res : Array.isArray(res?.data) ? res.data : []
        setOptions(data)
      })
      .catch(() => setOptions([]))
  }, [level])

  return (
    <Cascader
      options={options}
      value={value}
      onChange={(val) => onChange?.(val)}
      placeholder={placeholder}
      disabled={disabled}
      allowClear
      className={cn("w-full", className)}
    />
  )
}

export { RegionSelect }
