import { Separator } from "@/components/ui/separator"
import { SidebarTrigger } from "@/components/ui/sidebar"
import { Breadcrumbs } from "@/components/layout/breadcrumbs"
import { HeaderActions } from "@/components/layout/header-actions"
import { HeaderUser } from "@/components/layout/header-user"

export function SiteHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
      <div className="flex w-full items-center gap-1 px-3 sm:px-4 lg:gap-2 lg:px-6">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mx-1.5 sm:mx-2 h-4" />
        <Breadcrumbs />
        <div className="ml-auto flex items-center gap-0.5 sm:gap-1">
          <HeaderActions />
          <HeaderUser />
        </div>
      </div>
    </header>
  )
}
