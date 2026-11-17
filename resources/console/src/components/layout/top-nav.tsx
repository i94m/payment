"use client"

import { Link, useLocation } from "react-router-dom"
import { IconExternalLink } from "@tabler/icons-react"
import {
  NavigationMenu,
  NavigationMenuContent,
  NavigationMenuItem,
  NavigationMenuLink,
  NavigationMenuList,
  NavigationMenuTrigger,
  navigationMenuTriggerStyle,
} from "@/components/ui/navigation-menu"
import { Brand } from "@/components/layout/brand"
import { HeaderActions } from "@/components/layout/header-actions"
import { HeaderUser } from "@/components/layout/header-user"
import { MobileNav } from "@/components/layout/mobile-nav"
import { useMenuSections } from "@/hooks/use-menu-sections"
import { TOP_LAYOUT_MAX_WIDTH } from "@/lib/layout"
import { cn } from "@/lib/utils"

/**
 * 顶部导航（用户偏好 navPosition="top" 时启用）。
 *
 * 菜单形态：
 * - 无分组名（首页/数据统计）→ 直接链接
 * - 有分组名（交易管理……）→ 下拉触发器，子项在面板里
 * 窄屏（<lg）收进 MobileNav 抽屉。
 */
export function TopNav() {
  const currentPath = useLocation().pathname
  const sections = useMenuSections()

  // query 不参与高亮（与侧边栏按 pathname 判断保持一致）
  const isActive = (url: string) => url.split("?")[0] === currentPath
  const directItems = sections.filter((section) => !section.label).flatMap((section) => section.items)
  const groups = sections.filter((section) => section.label)

  return (
    <header className="sticky top-0 z-40 shrink-0 border-b bg-background/95 backdrop-blur">
      <div
        className={cn(
          "mx-auto flex h-14 w-full items-center gap-2 px-3 sm:px-4 lg:px-6",
          TOP_LAYOUT_MAX_WIDTH,
        )}
      >
        <MobileNav sections={sections} />
        <Brand className="mr-2 h-9 min-w-0 rounded-md px-3 hover:bg-muted/60 lg:mr-4" />

        <NavigationMenu viewport={false} className="hidden flex-1 justify-start lg:flex">
          <NavigationMenuList className="justify-start gap-0.5">
            {directItems.map((item) => {
              const active = isActive(item.url)
              return (
                <NavigationMenuItem key={`${item.title}-${item.url}`}>
                  <NavigationMenuLink
                    asChild
                    data-active={active}
                    className={cn(
                      navigationMenuTriggerStyle(),
                      "h-9 gap-1.5 px-3",
                      active && "bg-muted/60 text-primary",
                    )}
                  >
                    {item.external ? (
                      <a href={item.url} target="_blank" rel="noreferrer">
                        <item.icon className="size-4 shrink-0" />
                        <span>{item.title}</span>
                      </a>
                    ) : (
                      <Link to={item.url}>
                        <item.icon className="size-4 shrink-0" />
                        <span>{item.title}</span>
                      </Link>
                    )}
                  </NavigationMenuLink>
                </NavigationMenuItem>
              )
            })}

            {groups.map((section) => {
              const groupActive = section.items.some((item) => isActive(item.url))
              return (
                <NavigationMenuItem key={section.label}>
                  {/* 顶部菜单改为「点击展开」：Radix 默认在 pointermove 时展开子菜单，
                      这里 preventDefault 掉内建的 hover 展开（composeEventHandlers 会跳过），
                      点击（onItemSelect）与键盘 Enter/Space 仍然按开关处理。 */}
                  <NavigationMenuTrigger
                    onPointerMove={(e) => e.preventDefault()}
                    className={cn("h-9 px-3", groupActive && "bg-muted/60 text-primary")}
                  >
                    {section.label}
                  </NavigationMenuTrigger>
                  <NavigationMenuContent className="w-56 p-1.5 md:w-56">
                    <ul className="grid gap-0.5">
                      {section.items.map((item) => {
                        const active = isActive(item.url)
                        return (
                          <li key={item.title}>
                            <NavigationMenuLink
                              asChild
                              data-active={active}
                              className={cn("gap-2", active && "font-medium text-primary")}
                            >
                              {item.external ? (
                                <a href={item.url} target="_blank" rel="noreferrer">
                                  <item.icon className="size-4 shrink-0" />
                                  <span className="truncate">{item.title}</span>
                                  <IconExternalLink className="ml-auto size-3.5 text-muted-foreground" />
                                </a>
                              ) : (
                                <Link to={item.url}>
                                  <item.icon className="size-4 shrink-0" />
                                  <span className="truncate">{item.title}</span>
                                </Link>
                              )}
                            </NavigationMenuLink>
                          </li>
                        )
                      })}
                    </ul>
                  </NavigationMenuContent>
                </NavigationMenuItem>
              )
            })}
          </NavigationMenuList>
        </NavigationMenu>

        <div className="ml-auto flex items-center gap-1">
          <HeaderActions />
          <HeaderUser />
        </div>
      </div>
    </header>
  )
}
