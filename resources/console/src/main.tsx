// This file is kept for backward compatibility.
// The actual entry points are:
//   - src/apps/admin/main.tsx (admin module)
//   - src/apps/merchant/main.tsx (merchant module)
//   - src/apps/agent/main.tsx (agent module)
// If you hit this file directly, it defaults to admin.

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import "./styles/console-custom.css"
import App from "./App.tsx"
import { routes } from "./apps/admin/routes"
import { getAdminMenu } from "./apps/admin/sidebar"
import { filterMenu } from "./apps/merchant/sidebar"
import { useSiteStore } from "./stores/site-store"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App
      routes={routes}
      getMenu={() => filterMenu(getAdminMenu(), useSiteStore.getState().features)}
    />
  </StrictMode>,
)
