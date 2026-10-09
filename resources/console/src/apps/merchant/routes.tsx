import { lazy } from "react"
import type { RouteObject } from "react-router-dom"

// 路由级懒加载：各页面拆成独立 chunk，首屏只加载当前路由对应代码。
const DashboardPage = lazy(() => import("./pages/dashboard").then((m) => ({ default: m.DashboardPage })))
const OrdersPage = lazy(() => import("./pages/orders").then((m) => ({ default: m.OrdersPage })))
const SettlePage = lazy(() => import("./pages/settle").then((m) => ({ default: m.SettlePage })))
const RecordsPage = lazy(() => import("./pages/records").then((m) => ({ default: m.RecordsPage })))
const AgentsPage = lazy(() => import("./pages/agents").then((m) => ({ default: m.AgentsPage })))
const AgentPerformancePage = lazy(() => import("./pages/agent-performance").then((m) => ({ default: m.AgentPerformancePage })))
const PaypageMchPage = lazy(() => import("./pages/paypage").then((m) => ({ default: m.PaypageMchPage })))
const MessagesPage = lazy(() => import("./pages/messages").then((m) => ({ default: m.MessagesPage })))
const SecurityPage = lazy(() => import("./pages/security").then((m) => ({ default: m.SecurityPage })))
const PasswordPage = lazy(() => import("./pages/password").then((m) => ({ default: m.PasswordPage })))
const UserCenterPage = lazy(() => import("@/pages/user-center").then((m) => ({ default: m.UserCenterPage })))
// 错误页体积小且需在异常时立即可用，保持静态导入
import { Error403 } from "@/pages/errors/403"
import { Error404 } from "@/pages/errors/404"
import { Error500 } from "@/pages/errors/500"

// RouteObject in react-router v7 is a union type (PathRouteObject | IndexRouteObject),
// which an interface cannot extend. Use an intersection type instead.
export type PermissionRouteObject = RouteObject & {
  permission?: string
}

// 商户端单一角色：后端下发 permissions=["*"]，权限守卫全量放行
export const routes: PermissionRouteObject[] = [
  { path: "/", element: <DashboardPage /> },
  { path: "/user-center", element: <UserCenterPage /> },
  { path: "/orders", element: <OrdersPage /> },
  { path: "/settle", element: <SettlePage /> },
  { path: "/records", element: <RecordsPage /> },
  { path: "/agents", element: <AgentsPage /> },
  { path: "/agent-performance", element: <AgentPerformancePage /> },
  { path: "/paypage", element: <PaypageMchPage /> },
  { path: "/messages", element: <MessagesPage /> },
  { path: "/security", element: <SecurityPage /> },
  { path: "/password", element: <PasswordPage /> },
  { path: "/403", element: <Error403 /> },
  { path: "/500", element: <Error500 /> },
  { path: "*", element: <Error404 /> },
]
