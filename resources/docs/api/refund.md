# 申请退款

对已支付订单发起退款。采用**同步退款模型**：接口返回即终态——上游退款成功后
退款单落库、商户余额扣减在同一事务内完成；上游失败则报错且不产生任何副作用。

支持多次部分退款；累计退款达到可退上限后订单转为全额退款状态。

URL地址：`{site_url}/api/orders/{trade_no}/refund`

请求方式：`POST`（Content-Type: `application/json` 或 `application/x-www-form-urlencoded`，签名在参数内）

请求参数说明：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 平台订单号 | trade_no | 是 | String | 2609261200000000001234 | 路径参数 |
| 商户ID | pid | 是 | Int | 1001 | |
| 退款金额 | refund_amount | 否 | String | 0.50 | 单位：元，最多2位小数（超出自动四舍五入）；缺省 = 剩余可退全额 |
| 商户退款号 | out_refund_no | 否 | String | R20260926001 | 商户侧唯一退款单号，幂等键：重复提交返回原退款单 |
| 退款原因 | reason | 否 | String | 用户申请 | |
| 时间戳 | timestamp | 是 | Int | 1696000000 | Unix秒，偏差超过300秒拒绝（防重放） |
| 签名字符串 | sign | 是 | String | 9f2c1a...（MD5） | 签名算法[点此查看](/sign/index.html) |
| 签名类型 | sign_type | 是 | String | MD5 | `MD5`（默认）或 `RSA2`（需商户后台开启） |

退款规则：

- 订单须为**已支付**状态，未支付订单无法退款；
- 可退上限为该订单的**商户到账金额**（实付金额按商户费率折算后的到账金额，非订单申报金额），
  可通过[查单接口](/api/order-query.html)的 `refunded_amount` 与退款响应跟踪进度；
- 多次部分退款累计不能超过上限，超过将拒绝；
- `out_refund_no` 幂等：同一商户同一 `out_refund_no` 重复提交返回原退款单，不重复扣款；
- 商户余额小于退款金额时拒绝（退款从商户余额中扣回）；
- 上游通道退款失败时返回错误，**不落库、不扣余额**（零副作用），可稍后重试；
- 该接口受平台开关 `api_refund_enable` 控制，关闭时返回「退款接口未开放」。

返回结果（json）：

| 字段名 | 变量名 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|
| 返回状态码 | code | Int | 0 | 0为成功，其它值为失败 |
| 返回信息 | message | String | ok | 失败时返回原因 |
| 平台退款单号 | refund_no | String | R2609261200000000001001 | R 开头 22 位 |
| 商户退款号 | out_refund_no | String | R20260926001 | 未传时为空 |
| 平台订单号 | trade_no | String | 2609261200000000001234 | |
| 退款金额 | amount | String | 0.50 | |
| 退款状态 | status | Int | 1 | 1 为成功（同步模型，返回即成功） |
| 退款原因 | reason | String | 用户申请 | 未传时为空 |
| 创建时间 | created_at | String | 2026-09-26 12:00:00 | |
| 完成时间 | finished_at | String | 2026-09-26 12:00:01 | |

## 请求示例

```bash
curl -X POST "{site_url}/api/orders/2609261200000000001234/refund" \
  -H "Content-Type: application/json" \
  -d '{
    "pid": 1001,
    "refund_amount": "0.50",
    "out_refund_no": "R20260926001",
    "reason": "用户申请",
    "timestamp": "1696000000",
    "sign": "9f2c1a...（32位小写）",
    "sign_type": "MD5"
  }'
```

成功返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "refund_no": "R2609261200000000001001",
    "out_refund_no": "R20260926001",
    "trade_no": "2609261200000000001234",
    "amount": "0.50",
    "status": 1,
    "reason": "用户申请",
    "created_at": "2026-09-26 12:00:00",
    "finished_at": "2026-09-26 12:00:01"
  }
}
```

退款后可通过[查单接口](/api/order-query.html)查看订单 `refund_status`
（PARTIAL 部分退款 / FULL 全额退款）与 `refunded_amount`（累计退款金额）。

## 查询退款

URL地址：`{site_url}/api/refunds/{refund_no}`

请求方式：`GET`（认证参数放在 URL query 上）

请求参数说明：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 平台退款单号 | refund_no | 是 | String | R2609261200000000001001 | 路径参数 |
| 商户ID | pid | 是 | Int | 1001 | |
| 商户退款号 | out_refund_no | 否 | String | R20260926001 | 兜底查询：refund_no 未命中时按它查询 |
| 时间戳 | timestamp | 是 | Int | 1696000000 | Unix秒，偏差超过300秒拒绝 |
| 签名字符串 | sign | 是 | String | 9f2c1a...（MD5） | 签名算法[点此查看](/sign/index.html) |
| 签名类型 | sign_type | 是 | String | MD5 | `MD5`（默认）或 `RSA2`（需商户后台开启） |

返回字段与申请退款一致（查不到退款单时 `code` 非 0）。示例：

```bash
curl "{site_url}/api/refunds/R2609261200000000001001?pid=1001&timestamp=1696000000&sign=...&sign_type=MD5"
```

提示：仅保存了商户退款号（`out_refund_no`）时，可用
`{site_url}/api/refunds/none?out_refund_no=R20260926001&pid=...` 兜底查询。
