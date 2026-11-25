"use client"

import { IconChevronDown } from "@tabler/icons-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { UserMenuContent } from "@/components/layout/user-menu-content"
import { useAuthStore } from "@/stores/auth-store"

/** 顶栏用户入口：侧栏/顶栏两种布局都固定在页面右上角 */
export function HeaderUser() {
  const user = useAuthStore((s) => s.user)

  const name = user?.name || "User"
  const initial = name.charAt(0).toUpperCase()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="ghost" className="h-9 gap-1.5 px-1.5" />}>
        <Avatar className="size-7 rounded-lg">
          <AvatarImage src="" alt={name} />
          <AvatarFallback className="rounded-lg bg-primary/10 text-xs text-primary">{initial}</AvatarFallback>
        </Avatar>
        <span className="hidden max-w-28 truncate text-sm lg:inline">{name}</span>
        <IconChevronDown className="hidden size-3.5 text-muted-foreground lg:block" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" sideOffset={6} className="min-w-56 rounded-lg">
        <UserMenuContent />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
