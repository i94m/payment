# 回调通知

通知方式：服务器异步通知（`notify_url`，POST JSON Webhook）

## 异步通知（notify_url）

请求方式：`POST`（Content-Type: `application/json; charset=utf-8`）

用户支付成功后，平台会向下单时传入的 `notify_url` 发起 POST 请求，JSON body 携带以下参数：

| 字段名 | 变量名 | 必填 | 类型 | 示例值 | 描述 |
|---|---|---|---|---|---|
| 商户ID | pid | 是 | String | 1001 | |
| 平台订单号 | trade_no | 是 | String | 2609261200000000001234 | 平台订单号 |
| 商户订单号 | out_trade_no | 是 | String | 20160806151343349 | 商户系统内部的订单号 |
| 支付方式 | type | 是 | String | alipay | 平台已开通的支付方式编码 |
| 商品名称 | name | 是 | String | VIP会员 | |
| 商品金额 | money | 是 | String | 1.00 | 固定两位小数（如 `5.00`），验签请使用收到的参数原值 |
| 支付状态 | trade_status | 是 | String | TRADE_SUCCESS | 只有TRADE_SUCCESS是成功 |
| 业务扩展参数 | param | 否 | String | | 下单时传入原样返回，未传时无该字段 |
| 时间戳 | timestamp | 是 | String | 1696000000 | Unix秒 |
| 签名字符串 | sign | 是 | String | 9f2c1a... | MD5 订单用**商户密钥**签名；RSA2 订单用平台私钥签名，商户用**平台公钥**验签 |
| 签名类型 | sign_type | 是 | String | MD5 | 与下单时的 `sign_type` 一致：`MD5` / `RSA2` |

商户侧处理约定：

- 收到通知后，按 [签名算法](/sign/index.html) 验签：body 内参数去掉 `sign`、`sign_type` 后拼串，
  `sign_type=MD5` 时用商户密钥算 `md5(原文 + 密钥)` 比对，`sign_type=RSA2` 时用平台公钥验签；
  验签通过后再校验订单金额与状态，自行完成业务入账；
- **处理成功返回任意 HTTP 2xx 状态码**（200~299）即视为送达；
- 返回非 2xx（或超时/连接失败）的通知视为失败，平台将按
  **1 / 2 / 5 / 10 / 30 / 60 / 120 分钟** 的阶梯间隔重试，默认最多尝试 6 次（次数由平台配置）；
- 重试期间商户侧对 `timestamp` 的校验可按自身策略放宽；
- 通知即最终结果，收到成功通知后即可据此完成订单履约。

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
  "trade_no": "2609261200000000001234",
  "out_trade_no": "20160806151343349",
  "type": "alipay",
  "name": "VIP会员",
  "money": "1.00",
  "trade_status": "TRADE_SUCCESS",
  "param": "test_param",
  "timestamp": "1696000000",
  "sign": "9f2c1a...（32位小写）",
  "sign_type": "MD5"
}
```

## PHP 处理示例

```php
<?php
// Webhook 为 POST JSON body
$body = json_decode(file_get_contents('php://input'), true);
$merchantSecret = '你的MD5密钥（商户后台查看）';
$platformPublicKey = '平台公钥（商户后台查看，RSA2 订单才需要）';

function getSignContent(array $params): string {
    ksort($params);
    $parts = [];
    foreach ($params as $k => $v) {
        if ($k === 'sign' || $k === 'sign_type' || $v === '' || $v === null) continue;
        if (($k === 'money' || $k === 'refund_amount') && is_numeric($v)) {
            $v = number_format((float)$v, 2, '.', '');
        }
        $parts[] = $k . '=' . $v;
    }
    return implode('&', $parts);
}

$content = getSignContent($body);
if ($body['sign_type'] === 'MD5') {
    $ok = md5($content . $merchantSecret) === strtolower($body['sign']);
} else {
    $pem = "-----BEGIN PUBLIC KEY-----\n"
         . chunk_split($platformPublicKey, 64, "\n")
         . "-----END PUBLIC KEY-----";
    $ok = openssl_verify($content, base64_decode($body['sign']),
                         $pem, OPENSSL_ALGO_SHA256) === 1;
}

if ($ok && $body['trade_status'] === 'TRADE_SUCCESS') {
    // 验签通过：校验金额、入账 …… 处理成功返回 2xx
    http_response_code(200);
    echo 'OK';
} else {
    http_response_code(500); // 非 2xx 平台将按阶梯重试
}
```

其它语言（Python/Java/Node.js）的 MD5 签名与 RSA2 验签函数见 [签名算法](/sign/index.html)。
