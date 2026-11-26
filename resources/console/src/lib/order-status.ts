/**
 * 全平台统一的订单状态定义与配色。
 *
 * 状态口径与后端 `Order.status` 一致：0 待支付 / 1 已支付 / 2 已退款 / 3 已过期。
 * 色板固定写死，刻意避开主题色（primary）：品牌色会随站点配置变化，状态色必须稳定可辨。
 * 只上底色和文字色、不加边框，视觉上更轻。
 *
 * 参考色板（浅橙/浅蓝/浅绿/浅红/浅紫/浅灰），当前用到其中 4 个：
 *   待支付     中性·需行动  #FFF7E6 / #D46B08
 *   支付中     进行中·等待  #E6F4FF / #0958D9   ← 订单暂无此状态，通知「重试中」复用
 *   已支付     正向·完成    #F6FFED / #389E0D
 *   支付失败   负向·异常    #FFF2F0 / #CF1322   ← 订单暂无此状态，通知「失败」复用
 *   已退款     中性·回退    #F9F0FF / #722ED1
 *   已过期     终态·无行动  #FAFAFA / #8C8C8C
 *
 * 深色模式沿用同一色相（加深底色、提亮文字），不另起一套语义。
 * 新增状态请在这里补一行，不要在各页面里另写一套色板。
 */
export const ORDER_STATUS_OPTIONS = [
  {
    value: 0,
    label: "待支付",
    className: "bg-[#FFF7E6] text-[#D46B08] dark:bg-[#D46B08]/15 dark:text-[#FFA940]",
  },
  {
    value: 1,
    label: "已支付",
    className: "bg-[#F6FFED] text-[#389E0D] dark:bg-[#389E0D]/15 dark:text-[#73D13D]",
  },
  {
    value: 2,
    label: "已退款",
    className: "bg-[#F9F0FF] text-[#722ED1] dark:bg-[#722ED1]/20 dark:text-[#B37FEB]",
  },
  {
    value: 3,
    label: "已过期",
    className: "bg-[#FAFAFA] text-[#8C8C8C] dark:bg-[#8C8C8C]/15 dark:text-[#BFBFBF]",
  },
] as const

/** 后端出现未知状态（新枚举值/脏数据）时的兜底配色：与终态同一套中性灰 */
export const UNKNOWN_ORDER_STATUS_CLASS =
  "bg-[#FAFAFA] text-[#8C8C8C] dark:bg-[#8C8C8C]/15 dark:text-[#BFBFBF]"

/** 订单通知状态：failure=失败 / ok=已通知 / retry=重试中，与订单状态共用同一套语义色 */
export const NOTIFY_STATUS_CLASS = {
  failed: "bg-[#FFF2F0] text-[#CF1322] dark:bg-[#CF1322]/15 dark:text-[#FF7875]",
  ok: "bg-[#F6FFED] text-[#389E0D] dark:bg-[#389E0D]/15 dark:text-[#73D13D]",
  retry: "bg-[#E6F4FF] text-[#0958D9] dark:bg-[#0958D9]/20 dark:text-[#69B1FF]",
} as const

/** 后台订单列表的扣量行底色：与「已支付」同一档浅绿，只上底色、不改文字（商户/代理端不展示扣量单） */
export const DEDUCTED_ORDER_ROW_CLASS =
  "bg-[#F6FFED] hover:bg-[#EDF9DF] dark:bg-[#389E0D]/10 dark:hover:bg-[#389E0D]/15"

/** 状态徽标的公共外形，颜色由上面的 className 提供 */
export const STATUS_BADGE_BASE =
  "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap"
