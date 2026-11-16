/**
 * 主题预设清单（canonical registry）。
 *
 * `swatch` 取 index.css 中各 `[data-theme]` 块的 `--primary` 真实 oklch 值，
 * 改色相时两处需同步。`value` 是 index.css 的 `data-theme` 取值，直接用于
 * `document.documentElement.setAttribute("data-theme", value)`。
 *
 * 说明：默认主题（品牌橙）在 CSS 里是「无 data-theme 属性」，历史沿用的
 * store 键名是 "indigo"，这里保留该值以免破坏已持久化的用户选择；
 * 主题切换入口当前隐藏，`stores/theme-store.ts` 直接 import 本文件的
 * `VALID_THEMES` / `ColorTheme`，主题取值只有这一份来源。
 */
export const COLOR_THEMES = [
  { value: "indigo", label: "Default", labelZh: "默认（品牌橙）", swatch: "oklch(0.686 0.201 43.5)" },
  { value: "orange", label: "Orange", labelZh: "橙色", swatch: "oklch(0.553 0.195 38.402)" },
  { value: "yellow", label: "Yellow", labelZh: "明黄", swatch: "oklch(0.852 0.199 91.936)" },
  { value: "lime", label: "Lime", labelZh: "青柠", swatch: "oklch(0.841 0.238 128.85)" },
  { value: "cyan", label: "Cyan", labelZh: "青色", swatch: "oklch(0.52 0.105 223.128)" },
  { value: "sky", label: "Sky", labelZh: "天蓝", swatch: "oklch(0.5 0.134 242.749)" },
  { value: "purple", label: "Purple", labelZh: "紫色", swatch: "oklch(0.496 0.265 301.924)" },
  { value: "rose", label: "Rose", labelZh: "玫瑰", swatch: "oklch(0.514 0.222 16.935)" },
  { value: "stone", label: "Stone", labelZh: "石灰", swatch: "oklch(0.216 0.006 56.043)" },
] as const

export type ColorTheme = (typeof COLOR_THEMES)[number]["value"]
export const VALID_THEMES: ColorTheme[] = COLOR_THEMES.map((t) => t.value)

/** 是否为合法主题值（用于校验 localStorage / env 下发的值） */
export function isColorTheme(value: unknown): value is ColorTheme {
  return typeof value === "string" && (VALID_THEMES as string[]).includes(value)
}
