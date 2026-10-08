import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "@/index.css"
import App from "@/App.tsx"
import { routes } from "./routes"
import { getAdminMenu } from "./sidebar"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App routes={routes} getMenu={() => getAdminMenu()} />
  </StrictMode>,
)
