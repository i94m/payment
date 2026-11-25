import { lazy } from "react"
import type { RouteObject } from "react-router-dom"
import { FeatureGate } from "@/components/auth/feature-gate"
import { RouteErrorBoundary } from "@/components/route-error-boundary"

// 路由级懒加载：各页面拆成独立 chunk，首屏只加载当前路由对应代码。
// 页面均为命名导出，需映射为 default（React.lazy 要求 default 导出）。
const DashboardPage = lazy(() => import("./pages/dashboard").then((m) => ({ default: m.DashboardPage })))
const PerformancePage = lazy(() => import("./pages/report/performance").then((m) => ({ default: m.PerformancePage })))
const OrdersPage = lazy(() => import("./pages/trade/orders").then((m) => ({ default: m.OrdersPage })))
const NotifyLogsPage = lazy(() => import("./pages/trade/notifylogs").then((m) => ({ default: m.NotifyLogsPage })))
const MerchantsPage = lazy(() => import("./pages/merchants/index").then((m) => ({ default: m.MerchantsPage })))
const AdminRecordsPage = lazy(() => import("./pages/finance/records").then((m) => ({ default: m.AdminRecordsPage })))
const ChannelsPage = lazy(() => import("./pages/pay/channels").then((m) => ({ default: m.ChannelsPage })))
const PayTypesPage = lazy(() => import("./pages/pay/types").then((m) => ({ default: m.PayTypesPage })))
const ChannelCodesPage = lazy(() => import("./pages/pay/codes").then((m) => ({ default: m.ChannelCodesPage })))
const SettlePage = lazy(() => import("./pages/settle/list").then((m) => ({ default: m.SettlePage })))
const AnnouncesPage = lazy(() => import("./pages/announce/list").then((m) => ({ default: m.AnnouncesPage })))
const PaySettingsPage = lazy(() => import("./pages/setting/pay").then((m) => ({ default: m.PaySettingsPage })))
const AccountsPage = lazy(() => import("./pages/system/accounts").then((m) => ({ default: m.AccountsPage })))
const UpgradePage = lazy(() => import("./pages/system/upgrade").then((m) => ({ default: m.UpgradePage })))
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
  /** 仅超级管理员可进（后端该模块全部接口要求 super，避免子账号拿到权限键也只会 403） */
  superOnly?: boolean
}

// permission 与后端 PERMISSION_TREE 的 key 对齐
const pageRoutes: PermissionRouteObject[] = [
  { path: "/", element: <DashboardPage />, permission: "dashboard.view" },
  // 总业绩统计：按天看收款/扣量/利润，与首页统计点同口径（权限沿用 dashboard.view）
  { path: "/report/performance", element: <PerformancePage />, permission: "dashboard.view" },
  { path: "/user-center", element: <UserCenterPage /> },
  // 通知中心：按 message.view 权限放行（后端 message/* 接口同口径）
  { path: "/system/notifications", element: <AdminNotificationsPage />, permission: "message.view" },
  { path: "/trade/orders", element: <OrdersPage />, permission: "order.view" },
  { path: "/trade/notifylogs", element: <NotifyLogsPage />, permission: "notify_log.view" },
  { path: "/merchants", element: <MerchantsPage />, permission: "merchant.view" },
  { path: "/records", element: <AdminRecordsPage />, permission: "merchant.view" },
  { path: "/pay/channels", element: <ChannelsPage />, permission: "channel.view" },
  { path: "/pay/types", element: <PayTypesPage />, permission: "payment_type.view" },
  // 通道编码：独立菜单（成员 + 权重 + 开关塞进通道页签太难用）
  { path: "/pay/codes", element: <ChannelCodesPage />, permission: "channel_code.view" },
  { path: "/settle", element: <SettlePage />, permission: "settle.view" },
  // 公告管理：announce_enable（默认开启，仅库内可改）关闭后无发布入口、直链落到 404
  {
    path: "/announces", permission: "announce.view",
    element: <FeatureGate feature="announce_enable"><AnnouncesPage /></FeatureGate>,
  },
  { path: "/setting/pay", element: <PaySettingsPage />, permission: "pay_setting.view" },
  { path: "/system/accounts", element: <AccountsPage />, permission: "system.admin", superOnly: true },
  // 版本升级：镜像仓库在线查版本 + 一键升级（upgrader）/命令行指引；
  // 整机级操作（upgrader 持 docker.sock），与账号管理一致仅超管可进
  { path: "/system/upgrade", element: <UpgradePage />, permission: "system.admin", superOnly: true },
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
