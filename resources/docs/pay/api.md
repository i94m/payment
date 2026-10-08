# 支付下单

此接口可用于服务器后端发起支付请求，会返回支付二维码链接或支付跳转url。

URL地址：`{site_url}/api/orders`

请求方式：`POST`（Content-Type: `application/json`，签名在 body 参数内）

请求参数说明：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 商户ID | pid | 是 | Int | 1001 | |
| 支付方式 | type | 是 | String | alipay | [支付方式列表](/appendix/paytype.html) |
| 商户订单号 | out_trade_no | 是 | String | 20160806151343349 | 同一商户下唯一，最长64字符，重复请求幂等返回原订单 |
| 异步通知地址 | notify_url | 否 | String | http://www.pay.com/notify_url.php | 服务器异步通知地址，缺省时使用商户后台配置的默认通知地址 |
| 跳转通知地址 | return_url | 否 | String | http://www.pay.com/return_url.php | 页面跳转通知地址，缺省时使用商户后台配置的默认回跳地址 |
| 商品名称 | name | 是 | String | VIP会员 | 最长128字符，超出自动截取 |
| 商品金额 | money | 是 | String | 1.00 | 单位：元，最多2位小数（超出自动四舍五入） |
| 业务扩展参数 | param | 否 | String | 没有请留空 | 支付后原样返回，最长500字符 |
| 时间戳 | timestamp | 是 | Int | 1696000000 | Unix秒，偏差超过300秒拒绝（防重放） |
| 签名字符串 | sign | 是 | String | jKt+H3m...（Base64） | 签名算法[点此查看](/sign/rsa.html) |
| 签名类型 | sign_type | 是 | String | RSA2 | 固定为 RSA2 |

返回结果（json）：

| 字段名 | 变量名 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|
| 返回状态码 | code | Int | 0 | 0为成功，其它值为失败 |
| 返回信息 | message | String | ok | 失败时返回原因 |
| 订单号 | trade_no | String | 2609261200000000001234 | 平台订单号 |
| 商户订单号 | out_trade_no | String | 20160806151343349 | 商户系统内部的订单号 |
| 支付类型 | pay_type | String | qrcode | jump / qrcode / scheme / html |
| 支付跳转url | payurl | String | https://pay.upstream.com/submit.php?... | pay_type=jump 时返回（上游通道支付地址），直接跳转到该url支付 |
| 二维码链接 | qrcode | String | weixin://wxpay/bizpayurl?pr=04IPMKM | pay_type=qrcode 时返回，根据该url生成二维码 |
| 小程序跳转url | urlscheme | String | weixin://dl/business/?ticket=xxx | pay_type=scheme 时返回，使用js跳转该url可拉起小程序支付 |
| 收银台HTML | html | String | &lt;html&gt;... | pay_type=html 时返回，直接输出到页面渲染 |
| 收银台地址 | cashier_url | String | {site_url}/cashier/{不透明令牌} | 收银台地址，可引导用户跳转到该页面完成支付（收银台内可更换支付方式） |
| 订单有效期 | expires_in | Int | 300 | 订单有效期（秒） |

注：`payurl`、`qrcode`、`urlscheme`、`html` 四个参数只会返回其中一个（由 `pay_type` 决定）。

其它说明：

- 设备类型无需传入：服务端根据请求的 `User-Agent` 自动判断 PC / 移动端并匹配相应的上游支付形态；
- 若平台为商户配置了**支付域名白名单**，请求须携带合法的 `Referer` 或 `Origin` 且域名命中白名单（支持 `*.example.com` 通配），否则返回「来源域名未授权」；
- 下单接口有频率限制（默认 60 次/分钟，平台可调），超限返回失败；
- 拉起上游失败时本接口返回失败，但订单仍保留待支付状态（`trade_no` 有效），可稍后重试，或用返回的 `cashier_url` 收银台地址引导用户支付；
- 响应为纯 `{code, message, data}` 包络，不附带响应签名（请求认证已覆盖请求方向，响应回传在同一 TLS 连接内）。

## 请求示例

```bash
curl -X POST "{site_url}/api/orders" \
  -H "Content-Type: application/json" \
  -d '{
    "pid": 1001,
    "type": "alipay",
    "out_trade_no": "20160806151343349",
    "notify_url": "http://www.pay.com/notify_url.php",
    "return_url": "http://www.pay.com/return_url.php",
    "name": "VIP会员",
    "money": "1.00",
    "timestamp": 1696000000,
    "sign": "Base64编码的RSA2签名",
    "sign_type": "RSA2"
  }'
```

成功返回：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "trade_no": "2609261200000000001234",
    "out_trade_no": "20160806151343349",
    "pay_type": "qrcode",
    "qrcode": "weixin://wxpay/bizpayurl?pr=04IPMKM",
    "cashier_url": "{site_url}/cashier/gAAAAABl...（不透明令牌，长约120字符）",
    "expires_in": 300
  }
}
```

支付成功后可通过 [回调通知](/pay/notify.html)、[查询订单](/api/order-query.html) 获取结果，
通过 [申请退款](/api/refund.html) 接口退款。
