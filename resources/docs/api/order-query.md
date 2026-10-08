# 查询订单

URL地址：`{site_url}/orders/{trade_no}`

请求方式：`GET`（认证参数放在 URL query 上）

请求参数说明：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 平台订单号 | trade_no | 是 | String | 260926120000000001 | 路径参数 |
| 商户ID | pid | 是 | Int | 1001 | |
| 时间戳 | timestamp | 是 | Int | 1696000000 | Unix秒，偏差超过300秒拒绝（防重放） |
| 签名字符串 | sign | 是 | String | jKt+H3m...（Base64） | 签名算法[点此查看](/sign/rsa.html) |
| 签名类型 | sign_type | 是 | String | RSA2 | 固定为 RSA2 |

签名原文为 `pid + timestamp`（按参数名升序拼串后用商户私钥签名）。

返回结果（json）：

| 字段名 | 变量名 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|
| 返回状态码 | code | Int | 0 | 0为成功，其它值为失败 |
| 返回信息 | message | String | ok | 失败时返回原因 |
| 平台订单号 | trade_no | String | 260926120000000001 | 平台订单号 |
| 商户订单号 | out_trade_no | String | 20160806151343349 | 商户系统内部的订单号 |
| 支付方式 | type | String | alipay | [支付方式列表](/appendix/paytype.html) |
| 商品名称 | name | String | VIP会员 | |
| 商品金额 | money | String | 1.00 | 序列化去尾零 |
| 支付状态 | status | Int | 1 | 1为已支付（含已全额退款，退款状态见 refund_status），0为未支付 |
| 退款状态 | refund_status | String | PARTIAL | NONE 未退款 / PARTIAL 部分退款 / FULL 全额退款 |
| 累计退款金额 | refunded_amount | String | 1 | 序列化去尾零，仅已退款时有值 |
| 创建订单时间 | addtime | String | 2026-09-26 12:00:00 | |
| 完成交易时间 | endtime | String | 2026-09-26 12:00:01 | 未支付时为空 |

返回示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "trade_no": "260926120000000001",
    "out_trade_no": "20160806151343349",
    "type": "alipay",
    "name": "VIP会员",
    "money": "1",
    "status": 1,
    "refund_status": "PARTIAL",
    "refunded_amount": "0.5",
    "addtime": "2026-09-26 12:00:00",
    "endtime": "2026-09-26 12:00:01"
  }
}
```

提示：查单接口与异步通知结果一致，商户如对通知有疑义，应以查单接口结果为准。
退款相关操作见[申请退款](/api/refund.html)。
