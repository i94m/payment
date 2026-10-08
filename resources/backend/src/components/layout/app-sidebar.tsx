"use client"

import * as React from "react"
import { useLocation, Link } from "react-router-dom"
import { NavUser } from "./nav-user"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { useAuthStore } from "@/stores/auth-store"
import { useSiteStore } from "@/stores/site-store"
import { SidebarContext } from "@/App"
import type { MenuSection } from "@/apps/merchant/sidebar"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const location = useLocation()
  const currentPath = location.pathname
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const siteName = useSiteStore((s) => s.siteName)
  // 订阅 features 引用：商户端菜单按开关过滤（main.tsx 的 getMenu 内读取），
  // fetchSite 每次落新对象，开关变更后此处触发重渲染取到新菜单
  useSiteStore((s) => s.features)
  const { getMenu } = React.useContext(SidebarContext)

  const hasPermission = (key?: string) => {
    if (!key) return true
    if (user?.is_super_admin) return true
    // "*" = 全量放行（商户端单一角色由后端下发 permissions=["*"]）
    if (permissions.includes("*")) return true
    return permissions.includes(key)
  }

  const sections = getMenu().map((section) => ({
    ...section,
    items: section.items.filter((item) => hasPermission(item.permission)),
  }))

  return (
    <Sidebar collapsible="icon" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" tooltip={"仪表盘"}>
              <Link to="/">
                <img src="/images/logo.svg" alt="" className="size-5!" />
                <span className="truncate group-data-[collapsible=icon]:hidden">{siteName || "EPay"}</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {sections.map((section: MenuSection) => {
          if (!section.items.length) return null
          return (
            <SidebarGroup key={section.label ?? section.items[0].title} className="py-1 px-2">
              {section.label && <SidebarGroupLabel>{section.label}</SidebarGroupLabel>}
              <SidebarGroupContent className="flex flex-col gap-0.5">
                <SidebarMenu>
                  {section.items.map((item) => {
                    const active = item.url === currentPath

                    // External link (e.g. QQ群/APP下载): new tab, never active
                    if (item.external) {
                      return (
                        <SidebarMenuItem key={item.title}>
                          <SidebarMenuButton asChild tooltip={item.title} size="lg">
                            <a href={item.url} target="_blank" rel="noreferrer">
                              <item.icon className="shrink-0" />
                              <span className="truncate group-data-[collapsible=icon]:hidden">{item.title}</span>
                            </a>
                          </SidebarMenuButton>
                        </SidebarMenuItem>
                      )
                    }

                    return (
                      <SidebarMenuItem key={item.title}>
                        <SidebarMenuButton
                          asChild
                          tooltip={item.title}
                          size="lg"
                          isActive={active}
                        >
                          <Link to={item.url}>
                            <item.icon className="shrink-0" />
                            <span className="truncate group-data-[collapsible=icon]:hidden">{item.title}</span>
                          </Link>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    )
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          )
        })}
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}
