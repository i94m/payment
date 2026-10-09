import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "@/index.css"
import App from "@/App.tsx"
import { routes } from "./routes"
import { getAgentMenu } from "./sidebar"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* 代理面板无功能开关过滤（对齐老系统：菜单恒显） */}
    <App routes={routes} getMenu={() => getAgentMenu()} />
  </StrictMode>,
)
