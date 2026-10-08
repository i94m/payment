import { create } from "zustand"
import { STORAGE_PREFIX } from "@/lib/config"

type ColorTheme = "indigo" | "rose" | "orange" | "yellow" | "lime" | "cyan" | "purple" | "sky" | "stone"
type ColorMode = "light" | "dark"

const VALID_THEMES: ColorTheme[] = ["indigo", "rose", "orange", "yellow", "lime", "cyan", "purple", "sky", "stone"]

// 主题切换功能已隐藏：颜色模式固定为亮色
interface ThemeState {
  colorTheme: ColorTheme
  colorMode: ColorMode
}

function getInitialTheme(): ColorTheme {
  if (typeof window === "undefined") return "indigo"
  const saved = localStorage.getItem(`${STORAGE_PREFIX}color-theme`)
  if (saved && VALID_THEMES.includes(saved as ColorTheme)) return saved as ColorTheme
  const envDefault = import.meta.env.VITE_DEFAULT_THEME
  if (envDefault && VALID_THEMES.includes(envDefault as ColorTheme)) return envDefault as ColorTheme
  return "indigo"
}

function applyMode(mode: ColorMode) {
  const root = document.documentElement
  if (mode === "dark") {
    root.classList.add("dark")
  } else {
    root.classList.remove("dark")
  }
}

function applyTheme(theme: ColorTheme) {
  const root = document.documentElement
  if (theme === "indigo") {
    root.removeAttribute("data-theme")
  } else {
    root.setAttribute("data-theme", theme)
  }
}

export const useThemeStore = create<ThemeState>(() => {
  const initialTheme = getInitialTheme()
  applyMode("light")
  applyTheme(initialTheme)

  return {
    colorTheme: initialTheme,
    colorMode: "light",
  }
})

export type { ColorTheme, ColorMode }
