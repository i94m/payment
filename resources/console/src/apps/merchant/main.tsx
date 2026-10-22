import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "@/index.css"
import App from "@/App.tsx"
import { routes } from "./routes"
import { getMerchantMenu, filterMenu } from "./sidebar"
import { useSiteStore } from "@/stores/site-store"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* 菜单按 /site-info 下发的功能开关过滤；AppSidebar 订阅 features，
        fetchSite 落新对象后 getMenu 重新求值拿到新菜单 */}
    <App
      routes={routes}
      getMenu={() => filterMenu(getMerchantMenu(), useSiteStore.getState().features)}
    />
  </StrictMode>,
)
