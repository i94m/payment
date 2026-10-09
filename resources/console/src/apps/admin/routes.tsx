import { lazy } from "react"
import type { RouteObject } from "react-router-dom"

// 路由级懒加载：各页面拆成独立 chunk，首屏只加载当前路由对应代码。
// 页面均为命名导出，需映射为 default（React.lazy 要求 default 导出）。
const DashboardPage = lazy(() => import("./pages/dashboard").then((m) => ({ default: m.DashboardPage })))
const OrdersPage = lazy(() => import("./pages/trade/orders").then((m) => ({ default: m.OrdersPage })))
const NotifyLogsPage = lazy(() => import("./pages/trade/notifylogs").then((m) => ({ default: m.NotifyLogsPage })))
const MerchantsPage = lazy(() => import("./pages/merchants/index").then((m) => ({ default: m.MerchantsPage })))
const AdminRecordsPage = lazy(() => import("./pages/finance/records").then((m) => ({ default: m.AdminRecordsPage })))
const ChannelsPage = lazy(() => import("./pages/pay/channels").then((m) => ({ default: m.ChannelsPage })))
const PayTypesPage = lazy(() => import("./pages/pay/types").then((m) => ({ default: m.PayTypesPage })))
const SettlePage = lazy(() => import("./pages/settle/list").then((m) => ({ default: m.SettlePage })))
const AnnouncesPage = lazy(() => import("./pages/announce/list").then((m) => ({ default: m.AnnouncesPage })))
const PaySettingsPage = lazy(() => import("./pages/setting/pay").then((m) => ({ default: m.PaySettingsPage })))
const AccountsPage = lazy(() => import("./pages/system/accounts").then((m) => ({ default: m.AccountsPage })))
const AdminNotificationsPage = lazy(() => import("./pages/system/notifications").then((m) => ({ default: m.AdminNotificationsPage })))
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

// permission 与后端 PERMISSION_TREE 的 key 对齐
export const routes: PermissionRouteObject[] = [
  { path: "/", element: <DashboardPage /> },
  { path: "/user-center", element: <UserCenterPage /> },
  { path: "/system/notifications", element: <AdminNotificationsPage /> },
  { path: "/trade/orders", element: <OrdersPage />, permission: "order.view" },
  { path: "/trade/notifylogs", element: <NotifyLogsPage />, permission: "notify_log.view" },
  { path: "/merchants", element: <MerchantsPage />, permission: "merchant.view" },
  { path: "/records", element: <AdminRecordsPage />, permission: "merchant.view" },
  { path: "/pay/channels", element: <ChannelsPage />, permission: "channel.view" },
  { path: "/pay/types", element: <PayTypesPage />, permission: "payment_type.view" },
  { path: "/settle", element: <SettlePage />, permission: "settle.view" },
  { path: "/announces", element: <AnnouncesPage />, permission: "announce.view" },
  { path: "/setting/pay", element: <PaySettingsPage />, permission: "pay_setting.view" },
  { path: "/system/accounts", element: <AccountsPage />, permission: "system.admin" },
  { path: "/403", element: <Error403 /> },
  { path: "/500", element: <Error500 /> },
  { path: "*", element: <Error404 /> },
]
