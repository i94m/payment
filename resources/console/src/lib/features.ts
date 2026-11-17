// 前端静态功能开关（编译期常量）：只控制入口显隐，页面 / 路由 / 接口代码全部保留。
// 重新开放时把对应常量改回 true 即可，无需恢复任何被隐藏的代码。
// 与商户端 site-store 的 features（后端下发的运行时开关）不同，这里是代码级开关。

/** 后台通知中心（admin_messages）：侧边栏入口、顶栏铃铛、页面直链统一受此开关控制 */
export const FEATURE_NOTIFICATION_CENTER = false

/** 后台列表「导出 Excel」按钮：关闭时仅隐藏按钮，导出接口 / 处理函数代码全部保留 */
export const FEATURE_ADMIN_EXPORT_EXCEL = false
