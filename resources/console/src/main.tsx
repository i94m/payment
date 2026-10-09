// This file is kept for backward compatibility.
// The actual entry points are:
//   - src/apps/admin/main.tsx (admin module)
//   - src/apps/subadmin/main.tsx (subadmin module)
// If you hit this file directly, it defaults to admin.

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App.tsx"
import { routes } from "./apps/admin/routes"
import { getAdminMenu } from "./apps/admin/sidebar"

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App routes={routes} getMenu={() => getAdminMenu()} />
  </StrictMode>,
)
