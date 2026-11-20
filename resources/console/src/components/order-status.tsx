"use client"

import * as React from "react"
import {
  ORDER_STATUS_OPTIONS,
  STATUS_BADGE_BASE,
  UNKNOWN_ORDER_STATUS_CLASS,
} from "@/lib/order-status"

/** 订单状态徽标：配色定义在 lib/order-status.ts，全平台共用一套 */
export function OrderStatusBadge({ status }: { status: number }) {
  const meta = ORDER_STATUS_OPTIONS.find((o) => o.value === status)
  return (
    <span className={`${STATUS_BADGE_BASE} ${meta?.className ?? UNKNOWN_ORDER_STATUS_CLASS}`}>
      {meta?.label ?? `未知(${status})`}
    </span>
  )
}
