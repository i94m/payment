/**
 * 审计动作键 → 中文说明。
 *
 * 键是后端契约的一部分：`app/services/audit.record_audit(action=...)` 的取值，
 * 由各后台路由在业务提交后写入（merchant / channel / order / settings / settle）。
 * `GET /admin-api/audit/actions` 返回的也是这些键字符串（不是文案），所以中文
 * 只能在前端映射——录入时逐个核过 `record_audit(action=...)` 的实参。
 *
 * 未收录的键原样展示（见 `auditActionLabel`）：后端新增动作不需要改前端就能看见，
 * 想补中文时在这里加一行即可，不要做成「查不到就当空」。
 */
export const AUDIT_ACTION_LABELS: Record<string, string> = {
  // 商户
  "merchant.update": "修改商户资料",
  // 三个状态位分别有键：user=启用/停用、pay=支付开关、settle=结算开关、fixed=固定手续费
  "merchant.status_user": "切换商户启用状态",
  "merchant.status_pay": "切换商户支付状态",
  "merchant.status_settle": "切换商户结算状态",
  "merchant.status_fixed": "切换商户固定手续费",
  "merchant.password_reset": "重置商户密码",
  "merchant.balance_adjust": "调整商户余额",
  "merchant.delete": "删除商户",
  // 支付通道 / 插件
  "channel.create": "新增支付通道",
  "channel.update": "修改支付通道",
  "channel.delete": "删除支付通道",
  "plugin.install": "安装支付插件",
  // 交易 / 配置 / 结算
  "order.refill": "订单手动补单",
  "setting.update": "修改站点配置",
  "settle.pay": "结算打款",
  "settle.reject": "驳回结算",
}

/** 动作键的中文说明；未收录的键原样返回（空值显示破折号） */
export function auditActionLabel(action: string): string {
  if (!action) return "—"
  return AUDIT_ACTION_LABELS[action] ?? action
}
