import { create } from "zustand"
import { STORAGE_PREFIX } from "@/lib/config"
import { VALID_THEMES, type ColorTheme } from "@/lib/color-themes"

type ColorMode = "auto" | "light" | "dark"
type ResolvedMode = "light" | "dark"

interface ThemeState {
  colorTheme: ColorTheme
  colorMode: ColorMode
  /** auto 模式解析后的实际生效模式（sonner 等只认 light|dark 的消费方使用） */
  resolvedMode: ResolvedMode
  setColorTheme: (theme: ColorTheme) => void
  setColorMode: (mode: ColorMode) => void
}

function prefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches
}

function resolveMode(mode: ColorMode): ResolvedMode {
  if (mode === "auto") return prefersDark() ? "dark" : "light"
  return mode
}

function getInitialMode(): ColorMode {
  if (typeof window === "undefined") return "auto"
  const saved = localStorage.getItem(`${STORAGE_PREFIX}color-mode`)
  if (saved === "auto" || saved === "dark" || saved === "light") return saved
  // 无保存偏好时跟随系统深浅色；用户手动选择后以保存的偏好为准
  return "auto"
}

function getInitialTheme(): ColorTheme {
  if (typeof window === "undefined") return "indigo"
  const saved = localStorage.getItem(`${STORAGE_PREFIX}color-theme`)
  if (saved && (VALID_THEMES as string[]).includes(saved)) return saved as ColorTheme
  const envDefault = import.meta.env.VITE_DEFAULT_THEME
  if (envDefault && (VALID_THEMES as string[]).includes(envDefault)) return envDefault as ColorTheme
  return "indigo"
}

function applyMode(mode: ColorMode): ResolvedMode {
  const resolved = resolveMode(mode)
  document.documentElement.classList.toggle("dark", resolved === "dark")
  return resolved
}

function applyTheme(theme: ColorTheme) {
  const root = document.documentElement
  if (theme === "indigo") {
    root.removeAttribute("data-theme")
  } else {
    root.setAttribute("data-theme", theme)
  }
}

export const useThemeStore = create<ThemeState>((set) => {
  const initialMode = getInitialMode()
  const initialTheme = getInitialTheme()
  const resolvedMode = applyMode(initialMode)
  applyTheme(initialTheme)

  // auto 模式下跟随系统深浅色切换；显式选择 light/dark 时不响应
  if (typeof window !== "undefined") {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
      if (useThemeStore.getState().colorMode !== "auto") return
      document.documentElement.classList.toggle("dark", e.matches)
      useThemeStore.setState({ resolvedMode: e.matches ? "dark" : "light" })
    })
  }

  return {
    colorTheme: initialTheme,
    colorMode: initialMode,
    resolvedMode,
    setColorTheme: (theme) => {
      localStorage.setItem(`${STORAGE_PREFIX}color-theme`, theme)
      applyTheme(theme)
      set({ colorTheme: theme })
    },
    setColorMode: (mode) => {
      localStorage.setItem(`${STORAGE_PREFIX}color-mode`, mode)
      const resolved = applyMode(mode)
      set({ colorMode: mode, resolvedMode: resolved })
    },
  }
})

export type { ColorTheme, ColorMode, ResolvedMode }
