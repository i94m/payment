import { lazy } from "react"
import type { RouteObject } from "react-router-dom"

// 路由级懒加载：各页面拆成独立 chunk，首屏只加载当前路由对应代码。
const DashboardPage = lazy(() => import("./pages/dashboard").then((m) => ({ default: m.DashboardPage })))
const OrdersPage = lazy(() => import("./pages/orders").then((m) => ({ default: m.OrdersPage })))

// 错误页体积小且需在异常时立即可用，保持静态导入
import { Error403 } from "@/pages/errors/403"
import { Error404 } from "@/pages/errors/404"
import { Error500 } from "@/pages/errors/500"

// RouteObject in react-router v7 is a union type (PathRouteObject | IndexRouteObject),
// which an interface cannot extend. Use an intersection type instead.
export type PermissionRouteObject = RouteObject & {
  permission?: string
}

// 代理端单一角色：permissions=["*"]，权限守卫全量放行
export const routes: PermissionRouteObject[] = [
  { path: "/", element: <DashboardPage /> },
  { path: "/orders", element: <OrdersPage /> },
  { path: "/403", element: <Error403 /> },
  { path: "/500", element: <Error500 /> },
  { path: "*", element: <Error404 /> },
]
