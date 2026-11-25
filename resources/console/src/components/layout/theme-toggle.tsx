"use client"

import { useThemeStore, type ColorMode } from "@/stores/theme-store"
import { IconMoon, IconSun, IconDeviceDesktop } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

const MODES: { value: ColorMode; label: string; icon: typeof IconSun }[] = [
  { value: "auto", label: "自动", icon: IconDeviceDesktop },
  { value: "light", label: "亮色", icon: IconSun },
  { value: "dark", label: "暗色", icon: IconMoon },
]

/** 明暗模式切换：自动 / 亮色 / 暗色（参考 genesis 主题实现） */
export function ThemeToggle() {
  const { colorMode, setColorMode } = useThemeStore()
  const current = MODES.find((m) => m.value === colorMode) ?? MODES[0]
  const CurrentIcon = current.icon

  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" size="icon" className="size-8" />}>
        <CurrentIcon className="size-4" />
        <span className="sr-only">切换主题</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-32">
        {MODES.map((m) => (
          <DropdownMenuItem
            key={m.value}
            disabled={colorMode === m.value}
            onClick={() => setColorMode(m.value)}
          >
            <m.icon className="size-4" />
            <span className={cn(colorMode === m.value && "font-medium")}>{m.label}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
