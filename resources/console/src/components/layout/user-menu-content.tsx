"use client"

import {
  IconLayout,
  IconLayoutNavbar,
  IconLayoutSidebar,
  IconLogout,
  IconSettings,
} from "@tabler/icons-react"
import { useNavigate } from "react-router-dom"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import {
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuthStore } from "@/stores/auth-store"
import { useLayoutStore, type NavPosition } from "@/stores/layout-store"
import { APP_MODULE } from "@/lib/config"

/** 用户下拉内容：侧栏 footer 与顶栏头像共用 */
export function UserMenuContent() {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navPosition = useLayoutStore((s) => s.navPosition)
  const setNavPosition = useLayoutStore((s) => s.setNavPosition)

  const name = user?.name || "User"
  const email = user?.email || user?.username || ""
  const initial = name.charAt(0).toUpperCase()

  return (
    <>
      <DropdownMenuGroup>
        <DropdownMenuLabel className="p-0 font-normal">
          <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
            <Avatar className="h-8 w-8 rounded-lg">
              <AvatarImage src="" alt={name} />
              <AvatarFallback className="rounded-lg bg-primary/10 text-primary">{initial}</AvatarFallback>
            </Avatar>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium">{name}</span>
              <span className="truncate text-xs text-muted-foreground">{email}</span>
            </div>
          </div>
        </DropdownMenuLabel>
      </DropdownMenuGroup>

      {/* 商户端「账号信息」指向 /user-center（修改资料），侧边栏已下线该入口，
          这里一并隐藏；admin 端仍保留（账号信息 + 改密弹窗） */}
      {APP_MODULE === "admin" && (
        <>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem onClick={() => navigate("/user-center")}>
              <IconSettings />
              {"账号信息"}
            </DropdownMenuItem>
          </DropdownMenuGroup>
        </>
      )}

      {/* 布局偏好：仅存本地 localStorage，按模块各自记忆 */}
      <DropdownMenuSeparator />
      <DropdownMenuSub>
        <DropdownMenuSubTrigger>
          <IconLayout />
          菜单位置
        </DropdownMenuSubTrigger>
        <DropdownMenuSubContent className="min-w-40">
          <DropdownMenuRadioGroup
            value={navPosition}
            onValueChange={(value) => setNavPosition(value as NavPosition)}
          >
            <DropdownMenuRadioItem value="left">
              <IconLayoutSidebar />
              左侧菜单
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="top">
              <IconLayoutNavbar />
              顶部菜单
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuSubContent>
      </DropdownMenuSub>

      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={logout}>
        <IconLogout />
        {"退出登录"}
      </DropdownMenuItem>
    </>
  )
}
