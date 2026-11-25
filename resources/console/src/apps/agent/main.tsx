import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "@/index.css"
import "@/styles/console-custom.css"
import { AgentEntry } from "./entry"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* 代理面板菜单无开关过滤（对齐老系统：菜单恒显），agent_enable 只决定整个面板是否可用 */}
    <AgentEntry />
  </StrictMode>,
)
