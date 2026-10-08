# 支付结果通知

通知类型：服务器异步通知（notify_url，POST JSON Webhook）、页面跳转通知（return_url，GET 回跳）

## 异步通知（notify_url）

请求方式：`POST`（Content-Type: `application/json; charset=utf-8`）

用户支付成功后，平台会向下单时传入的 `notify_url` 发起 POST 请求，JSON body 携带以下参数：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 商户ID | pid | 是 | String | 1001 | |
| 平台订单号 | trade_no | 是 | String | 260926120000000001 | 平台订单号 |
| 商户订单号 | out_trade_no | 是 | String | 20160806151343349 | 商户系统内部的订单号 |
| 支付方式 | type | 是 | String | alipay | [支付方式列表](/appendix/paytype.html) |
| 商品名称 | name | 是 | String | VIP会员 | |
| 商品金额 | money | 是 | String | 1.00 | 序列化去尾零（如 `5` 而非 `5.00`），验签请使用收到的参数原值 |
| 支付状态 | trade_status | 是 | String | TRADE_SUCCESS | 只有TRADE_SUCCESS是成功 |
| 业务扩展参数 | param | 否 | String | | 下单时传入原样返回，未传时无该字段 |
| 时间戳 | timestamp | 是 | String | 1696000000 | Unix秒 |
| 签名字符串 | sign | 是 | String | Base64 | 平台私钥 RSA2 签名，用**平台公钥**验签 |
| 签名类型 | sign_type | 是 | String | RSA2 | 固定为 RSA2 |

商户侧处理约定：

- 收到通知后，先按 [RSA2签名算法](/sign/rsa.html#验签代码示例平台公钥) 用平台公钥验签
  （对 body 内参数去掉 `sign`、`sign_type` 后拼串验签），再校验订单金额与状态，自行完成业务入账；
- **处理成功返回任意 HTTP 2xx 状态码**（200~299）即视为送达；
- 返回非 2xx（或超时/连接失败）的通知视为失败，平台将按
  **1 / 2 / 5 / 10 / 30 / 60 / 120 分钟** 的阶梯间隔重试；
- 通知重试最长可达 2 小时，商户侧对 `timestamp` 的校验可按自身策略放宽；
- 通知与查单结果一致，商户如对通知有疑义，以[查单接口](/api/order-query.html)结果为准。

## 应答示例

```text
HTTP/1.1 200 OK
Content-Type: text/plain

OK
```

## 通知报文示例

```json
{
  "pid": "1001",
  "trade_no": "260926120000000001",
  "out_trade_no": "20160806151343349",
  "type": "alipay",
  "name": "VIP会员",
  "money": "1",
  "trade_status": "TRADE_SUCCESS",
  "param": "test_param",
  "timestamp": "1696000000",
  "sign": "Base64编码的RSA2签名",
  "sign_type": "RSA2"
}
```

## PHP 处理示例

```php
<?php
// Webhook 为 POST JSON body
$body = json_decode(file_get_contents('php://input'), true);
$platformPublicKey = '平台公钥（商户后台查看）';

ksort($body);
$parts = [];
foreach ($body as $k => $v) {
    if ($k === 'sign' || $k === 'sign_type' || $v === '' || $v === null) continue;
    $parts[] = $k . '=' . $v;
}
$pem = "-----BEGIN PUBLIC KEY-----\n"
     . chunk_split($platformPublicKey, 64, "\n")
     . "-----END PUBLIC KEY-----";
$ok = openssl_verify(implode('&', $parts), base64_decode($body['sign']),
                     $pem, OPENSSL_ALGO_SHA256) === 1;

if ($ok && $body['trade_status'] === 'TRADE_SUCCESS') {
    // 验签通过：校验金额、入账 …… 处理成功返回 2xx
    http_response_code(200);
    echo 'OK';
} else {
    http_response_code(500); // 非 2xx 平台将按阶梯重试
}
```

其它语言（Python/Node.js）验签函数见 [RSA2签名算法 - 验签代码示例](/sign/rsa.html#验签代码示例平台公钥)。

## 同步回跳（return_url）

支付成功后收银台会把用户浏览器重定向到下单时传入的 `return_url`（GET 请求，参数平铺在
URL query 上），参数与异步通知一致（`pid / trade_no / out_trade_no / type / name / money /
trade_status / param(未传时无) / timestamp / sign / sign_type=RSA2`），签名同样由平台私钥
RSA2 签发、可用平台公钥验签。

注意：return_url 仅用于用户浏览器端的支付完成展示，**不作为入账依据**（浏览器回跳可能被
用户伪造或放弃），入账请以异步通知或查单结果为准。
