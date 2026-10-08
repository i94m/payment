# 页面跳转支付

此接口可用于用户前台直接发起支付：商户服务端（或页面脚本）先调用创建订单接口，拿到收银台地址后引导用户跳转，用户在收银台内完成支付。

URL地址：`{site_url}/orders`

请求方式：`POST`（Content-Type: `application/json`，签名在 body 参数内）

请求参数说明：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 商户ID | pid | 是 | Int | 1001 | |
| 支付方式 | type | 是 | String | alipay | [支付方式列表](/appendix/paytype.html) |
| 商户订单号 | out_trade_no | 是 | String | 20160806151343349 | 同一商户下唯一，最长64字符，重复请求幂等返回原订单 |
| 异步通知地址 | notify_url | 否 | String | http://www.pay.com/notify_url.php | 服务器异步通知地址，缺省时使用商户后台配置的默认通知地址 |
| 跳转通知地址 | return_url | 否 | String | http://www.pay.com/return_url.php | 支付完成后收银台跳转地址，缺省时使用商户后台配置的默认回跳地址 |
| 商品名称 | name | 是 | String | VIP会员 | 最长128字符，超出自动截取 |
| 商品金额 | money | 是 | String | 1.00 | 单位：元，最大2位小数 |
| 业务扩展参数 | param | 否 | String | 没有请留空 | 支付后原样返回 |
| 时间戳 | timestamp | 是 | Int | 1696000000 | Unix秒，偏差超过300秒拒绝（防重放） |
| 签名字符串 | sign | 是 | String | jKt+H3m...（Base64） | 签名算法[点此查看](/sign/rsa.html) |
| 签名类型 | sign_type | 是 | String | RSA2 | 固定为 RSA2 |

返回结果（json）：

| 字段名 | 变量名 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|
| 返回状态码 | code | Int | 0 | 0为成功，其它值为失败 |
| 返回信息 | message | String | ok | 失败时返回原因 |
| 订单号 | trade_no | String | 260926120000000001 | 平台订单号 |
| 商户订单号 | out_trade_no | String | 20160806151343349 | 商户系统内部的订单号 |
| 收银台地址 | cashier_url | String | {site_url}/c/{不透明令牌} | **引导用户跳转到该地址完成支付** |
| 订单有效期 | expires_in | Int | 300 | 订单有效期（秒） |

支付流程：

1. 商户服务端调用本接口创建订单；
2. 将用户重定向到返回结果中的 `cashier_url`（收银台）；
3. 用户在收银台选择支付方式完成支付（可更换下单时指定的支付方式）；
4. 支付成功后，平台异步通知 `notify_url`，收银台自动跳转 `return_url`（若传入）。

注意：

- 支付结果请以 `notify_url` 异步通知或[查单接口](/api/order-query.html)为准，不要以 `return_url` 跳转为准；
- 用户未支付时订单保持待支付状态，有效期内可再次引导用户打开 `cashier_url` 继续支付。
