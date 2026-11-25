"use client"

import * as React from "react"
import { format, getYear, getMonth, setYear as setYearFns, setMonth } from "date-fns"
import { zhCN } from "date-fns/locale/zh-CN"
import { CalendarIcon } from "lucide-react"

import type { DateRange } from "react-day-picker"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

interface DatePickerProps {
  value?: Date | undefined
  onChange?: (date: Date | undefined) => void
  placeholder?: string
  className?: string
  disabled?: boolean
  /** Display format passed to date-fns format(). Default: "yyyy-MM-dd" */
  displayFormat?: string
  /** Picker mode: "date" shows full calendar, "month" shows month grid, "year" shows year grid */
  pickerMode?: "date" | "month" | "year"
}

function DatePicker({
  value,
  onChange,
  placeholder = "选择日期",
  className,
  disabled,
  displayFormat,
  pickerMode = "date",
}: DatePickerProps) {
  const [open, setOpen] = React.useState(false)

  const handleSelect = React.useCallback((date: Date) => {
    onChange?.(date)
    setOpen(false)
  }, [onChange])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline"
          disabled={disabled}
          className={cn(
            "w-full justify-start text-left font-normal",
            !value && "text-muted-foreground",
            className
          )} />}><CalendarIcon className="mr-2 size-4" />
          {value ? format(value, displayFormat ?? "yyyy-MM-dd") : placeholder}</PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        {pickerMode === "year" ? (
          <YearGrid value={value} onSelect={handleSelect} />
        ) : pickerMode === "month" ? (
          <MonthGrid value={value} onSelect={handleSelect} />
        ) : (
          <Calendar
            mode="single"
            selected={value}
            onSelect={(date) => {
              onChange?.(date)
              setOpen(false)
            }}
          />
        )}
      </PopoverContent>
    </Popover>
  )
}

/* ---- Year grid ---- */
function YearGrid({
  value,
  onSelect,
}: {
  value?: Date | undefined
  onSelect: (date: Date) => void
}) {
  const currentYear = getYear(value ?? new Date())
  const [page, setPage] = React.useState(() => Math.floor(currentYear / 20) * 20)

  const years = React.useMemo(() => {
    const arr: number[] = []
    for (let y = page; y < page + 20; y++) arr.push(y)
    return arr
  }, [page])

  return (
    <div className="p-3 w-[260px]">
      <div className="flex items-center justify-between mb-2">
        <Button variant="ghost" size="icon" className="size-7" onClick={() => setPage((p) => p - 20)}>
          ‹
        </Button>
        <span className="text-sm font-medium">{page}–{page + 19}</span>
        <Button variant="ghost" size="icon" className="size-7" onClick={() => setPage((p) => p + 20)}>
          ›
        </Button>
      </div>
      <div className="grid grid-cols-5 gap-1">
        {years.map((y) => (
          <Button
            key={y}
            variant={y === currentYear ? "default" : "ghost"}
            className="h-9 rounded-md text-sm"
            onClick={() => onSelect(setYearFns(value ?? new Date(), y))}
          >
            {y}
          </Button>
        ))}
      </div>
    </div>
  )
}

/* ---- Month grid ---- */
function MonthGrid({
  value,
  onSelect,
}: {
  value?: Date | undefined
  onSelect: (date: Date) => void
}) {
  const locale = zhCN
  const currentYear = getYear(value ?? new Date())
  const currentMonth = getMonth(value ?? new Date())
  const [viewYear, setViewYear] = React.useState(currentYear)

  const months = React.useMemo(
    () => Array.from({ length: 12 }, (_, i) => ({
      value: i,
      label: format(new Date(2000, i), "MMM", { locale }),
    })),
    [locale],
  )

  return (
    <div className="p-3 w-[220px]">
      <div className="flex items-center justify-between mb-2">
        <Button variant="ghost" size="icon" className="size-7" onClick={() => setViewYear((y) => y - 1)}>
          ‹
        </Button>
        <span className="text-sm font-medium">{viewYear}</span>
        <Button variant="ghost" size="icon" className="size-7" onClick={() => setViewYear((y) => y + 1)}>
          ›
        </Button>
      </div>
      <div className="grid grid-cols-3 gap-1">
        {months.map((m) => (
          <Button
            key={m.value}
            variant={viewYear === currentYear && m.value === currentMonth ? "default" : "ghost"}
            className="h-9 rounded-md text-sm"
            onClick={() => onSelect(setMonth(setYearFns(value ?? new Date(), viewYear), m.value))}
          >
            {m.label}
          </Button>
        ))}
      </div>
    </div>
  )
}

export { DatePicker }

/* ---- Date Range Picker ---- */

export interface DateRangePickerProps {
  /** [from, to] tuple */
  value: [Date | undefined, Date | undefined]
  onChange: (range: [Date | undefined, Date | undefined]) => void
  startPlaceholder?: string
  endPlaceholder?: string
  className?: string
  disabled?: boolean
  displayFormat?: string
}

function DateRangePicker({
  value,
  onChange,
  startPlaceholder = "开始日期",
  endPlaceholder = "结束日期",
  className,
  disabled,
  displayFormat = "yyyy-MM-dd",
}: DateRangePickerProps) {
  const [open, setOpen] = React.useState(false)
  const fmt = displayFormat

  const dateRange: DateRange = React.useMemo(
    () => ({ from: value[0], to: value[1] }),
    [value],
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger render={<Button variant="outline"
          disabled={disabled}
          className={cn(
            "w-full justify-start text-left font-normal",
            !value[0] && !value[1] && "text-muted-foreground",
            className
          )} />}><CalendarIcon className="mr-2 size-4" />
          {value[0] ? (
            <>
              {format(value[0], fmt)}
              <span className="mx-1 text-muted-foreground">–</span>
              {value[1] ? format(value[1], fmt) : endPlaceholder}
            </>
          ) : (
            startPlaceholder
          )}</PopoverTrigger>
      <PopoverContent className="w-auto p-0" align="start">
        <Calendar
          mode="range"
          selected={dateRange}
          onSelect={(range) => {
            onChange([range?.from, range?.to])
            if (range?.from && range?.to) setOpen(false)
          }}
          numberOfMonths={2}
        />
      </PopoverContent>
    </Popover>
  )
}

export { DateRangePicker }
