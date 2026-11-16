import { lazy } from "react"
import type { RouteObject } from "react-router-dom"
// 通知中心入口隐藏开关（见 lib/features）：关闭时仅移除入口与直链可达性，页面代码保留
import { FEATURE_NOTIFICATION_CENTER } from "@/lib/features"
import { FeatureGate } from "@/components/auth/feature-gate"
import { RouteErrorBoundary } from "@/components/route-error-boundary"

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
const AuditPage = lazy(() => import("./pages/system/audit").then((m) => ({ default: m.AuditPage })))
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
const pageRoutes: PermissionRouteObject[] = [
  { path: "/", element: <DashboardPage /> },
  { path: "/user-center", element: <UserCenterPage /> },
  // 通知中心：入口隐藏后直链落到 404（页面组件保留在 AdminNotificationsPage）
  { path: "/system/notifications", element: FEATURE_NOTIFICATION_CENTER ? <AdminNotificationsPage /> : <Error404 /> },
  { path: "/trade/orders", element: <OrdersPage />, permission: "order.view" },
  { path: "/trade/notifylogs", element: <NotifyLogsPage />, permission: "notify_log.view" },
  { path: "/merchants", element: <MerchantsPage />, permission: "merchant.view" },
  { path: "/records", element: <AdminRecordsPage />, permission: "merchant.view" },
  { path: "/pay/channels", element: <ChannelsPage />, permission: "channel.view" },
  { path: "/pay/types", element: <PayTypesPage />, permission: "payment_type.view" },
  { path: "/settle", element: <SettlePage />, permission: "settle.view" },
  // 公告管理：announce_enable（默认开启，仅库内可改）关闭后无发布入口、直链落到 404
  {
    path: "/announces", permission: "announce.view",
    element: <FeatureGate feature="announce_enable"><AnnouncesPage /></FeatureGate>,
  },
  { path: "/setting/pay", element: <PaySettingsPage />, permission: "pay_setting.view" },
  { path: "/system/accounts", element: <AccountsPage />, permission: "system.admin" },
  // 操作审计：只读，权限键 system.audit（与后端 PermissionChecker 同口径）
  { path: "/system/audit", element: <AuditPage />, permission: "system.audit" },
  { path: "/403", element: <Error403 /> },
  { path: "/500", element: <Error500 /> },
  { path: "*", element: <Error404 /> },
]

// 每个页面套一层路由级错误边界：单页渲染崩溃（含 lazy chunk 加载失败）只替换内容区，
// 侧边栏/顶栏保留，切换路由自动复位；整站仍然有 App 级 ErrorBoundary 兜底。
export const routes: PermissionRouteObject[] = pageRoutes.map((route) =>
  route.element
    ? { ...route, element: <RouteErrorBoundary>{route.element}</RouteErrorBoundary> }
    : route,
)
