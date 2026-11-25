"use client"

import * as React from "react"
import { Link, useLocation } from "react-router-dom"
import { IconMenu2 } from "@tabler/icons-react"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Brand } from "@/components/layout/brand"
import { cn } from "@/lib/utils"
import type { MenuSection } from "@/apps/merchant/sidebar"

/** 顶栏模式的移动端菜单：独立 Sheet，不复用依赖 SidebarProvider 的侧栏组件 */
export function MobileNav({ sections }: { sections: MenuSection[] }) {
  const [open, setOpen] = React.useState(false)
  const currentPath = useLocation().pathname
  const close = () => setOpen(false)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        render={<Button variant="ghost" size="icon" className="size-9 lg:hidden" aria-label="打开菜单" />}
      >
        <IconMenu2 className="size-5" />
      </SheetTrigger>
      <SheetContent side="left" className="gap-0 p-0">
        <SheetHeader className="border-b px-4 py-3">
          <SheetTitle className="sr-only">导航菜单</SheetTitle>
          <Brand onNavigate={close} />
        </SheetHeader>
        <nav className="flex-1 overflow-y-auto p-3">
          {sections.map((section) => (
            <div key={section.label ?? section.items[0]?.title} className="mb-4 last:mb-0">
              {section.label && (
                <div className="px-2 pb-1 text-xs font-medium text-muted-foreground">{section.label}</div>
              )}
              <div className="flex flex-col gap-0.5">
                {section.items.map((item) => {
                  const active = item.url === currentPath
                  const itemClass = cn(
                    "flex items-center gap-2 rounded-md px-2 py-2 text-sm transition-colors hover:bg-muted",
                    active && "bg-muted font-medium text-primary",
                  )

                  // 外链（QQ群/APP下载/开发文档）：新窗口打开，不参与高亮
                  if (item.external) {
                    return (
                      <a
                        key={item.title}
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={close}
                        className={itemClass}
                      >
                        <item.icon className="size-4 shrink-0" />
                        <span className="truncate">{item.title}</span>
                      </a>
                    )
                  }

                  return (
                    <Link key={item.title} to={item.url} onClick={close} className={itemClass}>
                      <item.icon className="size-4 shrink-0" />
                      <span className="truncate">{item.title}</span>
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>
      </SheetContent>
    </Sheet>
  )
}
