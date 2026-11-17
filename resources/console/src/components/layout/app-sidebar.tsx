"use client"

import * as React from "react"
import { useLocation, Link } from "react-router-dom"
import { Brand } from "./brand"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import { useMenuSections } from "@/hooks/use-menu-sections"

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const currentPath = useLocation().pathname
  // 权限 / 静态 hidden / 功能开关统一的菜单过滤，顶栏模式共用同一份结果
  const sections = useMenuSections()

  return (
    <Sidebar collapsible="icon" variant="inset" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" tooltip={"数据统计"}>
              <Brand inSidebar />
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {sections.map((section) => {
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
    </Sidebar>
  )
}
