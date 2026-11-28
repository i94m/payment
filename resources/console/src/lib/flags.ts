/**
 * 构建期功能开关（前端隐藏按钮；后端接口不受影响）
 *
 * 导出：商户列表 / 订单管理 / 资金明细 / 商户端订单 的「导出 Excel」按钮。
 * 随机金额/限流：商户列表的列、批量入口与设置弹窗（后端配置与建单校验继续生效）。
 * 商户状态/支付/结算/配置锁定：商户列表的列与新增/编辑弹窗里的开关（后端接口保留）。
 * 费率/余额调整：商户列表行内入口与新增/编辑弹窗（后端接口保留）。
 *
 * 需要恢复某项功能时改回 true 即可，页面代码无需改动。
 */

/** 导出 Excel 按钮是否展示 */
export const EXPORT_ENABLED = false

/** 随机金额 / 建单限流 是否展示（商户管理入口与列） */
export const RANDOM_AMOUNT_ENABLED = false

/** 商户状态 / 支付 / 结算 / 配置锁定 是否展示（列与新增/编辑设置） */
export const MERCHANT_STATUS_ENABLED = false

/** 商户费率 / 余额调整 是否展示（新增/编辑表单里的费率、行内「调整余额」与余额点击入口） */
export const MERCHANT_FINANCE_ENABLED = false
