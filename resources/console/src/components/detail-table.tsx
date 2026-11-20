"use client"

import * as React from "react"

export type DetailCell = {
  label: string
  value?: React.ReactNode
  icon?: React.ReactNode
  /** 等宽小字号（订单号/IP 这类机器生成的值） */
  mono?: boolean
  /** 允许在任意位置断行（URL、扩展参数这类长串） */
  wrap?: boolean
}

/**
 * 详情弹窗通用的「字段 / 值」表：
 * - 桌面一行两组，窄屏自动退化成一行一组，避免四列挤在一起；
 * - 空值统一渲染成正文尺寸的「—」，不跟着 mono 单元格变成等宽小字号。
 */
export function DetailTable({ rows }: { rows: Array<[DetailCell, DetailCell]> }) {
  return (
    <dl className={[
      "grid grid-cols-[auto_minmax(0,1fr)] overflow-hidden rounded-lg border text-sm",
      "sm:grid-cols-[7.5rem_minmax(0,1fr)_7.5rem_minmax(0,1fr)]",
      // 最后一行贴边，避免和容器边框叠成双线
      "[&>*:nth-last-child(-n+2)]:border-b-0",
    ].join(" ")}>
      {rows.flatMap(([left, right], i) => [
        <DetailLabel key={`l${i}`} cell={left} />,
        <DetailValue key={`v${i}`} cell={left} />,
        <DetailLabel key={`l${i}r`} cell={right} divided />,
        <DetailValue key={`v${i}r`} cell={right} divided />,
      ])}
    </dl>
  )
}

function DetailLabel({ cell, divided }: { cell: DetailCell; divided?: boolean }) {
  return (
    <dt className={`border-b border-r bg-muted/40 px-3 py-2 text-xs text-muted-foreground whitespace-nowrap ${divided ? "sm:border-l" : ""}`}>
      {cell.label}
    </dt>
  )
}

function DetailValue({ cell, divided }: { cell: DetailCell; divided?: boolean }) {
  const empty = cell.value === undefined || cell.value === null || cell.value === ""
  return (
    <dd className={[
      "min-w-0 border-b px-3 py-2 break-words",
      !empty && cell.mono ? "font-mono text-xs" : "",
      !empty && cell.wrap ? "break-all" : "",
      divided ? "sm:border-l" : "",
    ].join(" ")}
        title={typeof cell.value === "string" && cell.value ? cell.value : undefined}>
      {empty ? (
        <span className="text-muted-foreground">—</span>
      ) : cell.icon ? (
        <span className="inline-flex items-center gap-1.5">{cell.icon}{cell.value}</span>
      ) : cell.value}
    </dd>
  )
}
