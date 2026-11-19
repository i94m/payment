# 签名算法

开放 API 只有一套接口，`sign_type` 决定本次请求使用哪种算法——不是版本号，
所有端点的 URL、参数、响应结构完全相同：

| 算法 | sign_type | 默认 | 说明 |
|---|---|---|---|
| **MD5** | `MD5` | ✅ 开箱可用 | 商户创建即生成 32 位密钥，`md5(签名原文 + 密钥)` 小写 |
| RSA2 | `RSA2` | 需商户后台开启 | SHA256WithRSA + Base64，商户自行保管私钥，平台只存公钥 |

- `sign_type` **必填**，取值只有 `MD5` / `RSA2`（大小写不敏感，服务端统一转大写）
- 签名放在 **body 参数内**（JSON 或表单均可，支付宝风格），无需 Authorization 头
- `timestamp` **必填**：Unix 秒，与服务器偏差超过 300 秒拒绝（防重放）
- 同一份签名 300 秒内只受理一次（防止截获重放）；**重试请刷新 `timestamp` 并重新签名**

## 签名原文

两种算法共用同一份签名原文：

1. 去掉 `sign`、`sign_type`，去掉空值参数（`null`、空字符串，含纯空白；`"0"` / `0` 保留）；
2. 其余参数按参数名 ASCII 升序排序，拼成 `k1=v1&k2=v2&...`（值不 URL 编码）；
3. 金额字段 `money`、`refund_amount` 统一两位小数（`1`、`"1"`、`1.0` 都按 `1.00` 参与拼串）。

> 参数值请按字符串传，示例一律带引号；数字与字符串混用不会改变拼串结果，但金额请统一写成两位小数。

示例（待签名参数）：

```json
{
  "pid": "1001",
  "type": "alipay",
  "out_trade_no": "20160806151343349",
  "notify_url": "http://www.pay.com/notify_url.php",
  "name": "VIP会员",
  "money": "1.00",
  "timestamp": "1696000000"
}
```

签名原文：

```text
money=1.00&name=VIP会员&notify_url=http://www.pay.com/notify_url.php&out_trade_no=20160806151343349&pid=1001&timestamp=1696000000&type=alipay
```

## MD5（默认）

签名规则与易支付一致：**密钥做后缀**，`md5(签名原文 + 密钥)` 取 32 位小写 hex。

```text
sign = md5("money=1.00&name=VIP会员&...&type=alipay" + "你的32位密钥")
```

- 密钥在商户后台「安全设置 → 接口密钥」查看/复制，可随时重置（重置后旧密钥立即失效）
- 密钥只在服务端使用，泄漏等于他人可代你下单/退款；务必走 HTTPS
- 商户后台提供**签名自检**：粘贴待发参数，直接给出签名原文与期望 MD5 值
- MD5 只做防篡改与身份识别，不提供抗抵赖；需要争议取证请开启 RSA2

### 请求示例（MD5）

```bash
curl -X POST "{site_url}/api/orders" \
  -H "Content-Type: application/json" \
  -d '{
    "pid": "1001",
    "type": "alipay",
    "out_trade_no": "20160806151343349",
    "notify_url": "http://www.pay.com/notify_url.php",
    "name": "VIP会员",
    "money": "1.00",
    "timestamp": "1696000000",
    "sign": "9f2c1a...（32位小写）",
    "sign_type": "MD5"
  }'
```

### 代码示例

PHP：

```php
function getSignContent(array $params): string {
    ksort($params);
    $parts = [];
    foreach ($params as $k => $v) {
        if ($k === 'sign' || $k === 'sign_type' || $v === null || trim((string)$v) === '') continue;
        if (($k === 'money' || $k === 'refund_amount') && is_numeric($v)) {
            $v = number_format((float)$v, 2, '.', '');
        }
        $parts[] = $k . '=' . $v;
    }
    return implode('&', $parts);
}

$sign = md5(getSignContent($params) . $merchantSecret);   // 32 位小写
```

Python：

```python
import hashlib
from decimal import Decimal

MONEY_FIELDS = ("money", "refund_amount")

def get_sign_content(params: dict) -> str:
    parts = {}
    for k, v in params.items():
        if k in ("sign", "sign_type") or v is None or str(v).strip() == "":
            continue
        if k in MONEY_FIELDS:
            v = f"{Decimal(str(v)):.2f}"
        parts[k] = str(v)
    return "&".join(f"{k}={parts[k]}" for k in sorted(parts))

def make_md5_sign(params: dict, secret: str) -> str:
    return hashlib.md5((get_sign_content(params) + secret).encode("utf-8")).hexdigest()
```

Java：

```java
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

static String getSignContent(Map<String, Object> params) {
    TreeMap<String, Object> sorted = new TreeMap<>(params);
    StringBuilder sb = new StringBuilder();
    for (Map.Entry<String, Object> e : sorted.entrySet()) {
        String k = e.getKey();
        Object v = e.getValue();
        if (k.equals("sign") || k.equals("sign_type") || v == null) continue;
        if (v.toString().trim().isEmpty()) continue;
        if (k.equals("money") || k.equals("refund_amount")) {
            v = new BigDecimal(v.toString()).setScale(2, BigDecimal.ROUND_HALF_UP).toPlainString();
        }
        if (sb.length() > 0) sb.append('&');
        sb.append(k).append('=').append(v);
    }
    return sb.toString();
}

static String makeMd5Sign(Map<String, Object> params, String secret) throws Exception {
    byte[] digest = MessageDigest.getInstance("MD5")
        .digest((getSignContent(params) + secret).getBytes(StandardCharsets.UTF_8));
    StringBuilder hex = new StringBuilder();
    for (byte b : digest) hex.append(String.format("%02x", b));
    return hex.toString();
}
```

Node.js：

```javascript
const crypto = require("crypto")

function getSignContent(params) {
  return Object.keys(params)
    .filter((k) => !["sign", "sign_type"].includes(k))
    .filter((k) => params[k] != null && String(params[k]).trim() !== "")
    .sort()
    .map((k) => {
      let v = params[k]
      if ((k === "money" || k === "refund_amount") && !Number.isNaN(Number(v))) {
        v = Number(v).toFixed(2)
      }
      return `${k}=${v}`
    })
    .join("&")
}

function makeMd5Sign(params, secret) {
  return crypto.createHash("md5").update(getSignContent(params) + secret, "utf8").digest("hex")
}
```

## RSA2（可选，需商户后台开启）

开启后使用 SHA256WithRSA（PKCS#1 v1.5）签名，结果 Base64 编码。商户自行生成密钥对，
**私钥自己保管，平台只存公钥**；开启步骤：

1. 商户后台「安全设置 → 接口密钥 → RSA2 签名」生成密钥对，或上传自行生成的公钥；
2. 打开 RSA2 开关；
3. 调用时 `sign_type=RSA2`，用私钥对同一份签名原文签名。

```text
sign = base64(RSA_SHA256_sign(签名原文, 商户私钥))
```

> RSA2 只在入站请求上受开关控制；出站通知按订单下单时使用的算法签名，
> 因此关闭开关不会影响历史 RSA2 订单的通知验签。

### 通知与回跳

通知（`notify_url`）与回跳（`return_url`）的报文都带 `sign_type`，商户按它选择验签函数：

| 订单的 sign_type | 通知签名密钥 | 商户验签密钥 |
|---|---|---|
| `MD5`（默认） | 商户密钥（`md5(原文 + 密钥)`） | 同一把商户密钥 |
| `RSA2` | 平台私钥 | 平台公钥（商户后台查看） |

即：MD5 下单的通知用你配置的同一把 MD5 密钥验签；RSA2 下单的通知用平台公钥验签。

### 验签示例（RSA2 通知）

Python：

```python
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

def verify_rsa_sign(params: dict, public_key_b64: str, sign: str) -> bool:
    content = get_sign_content(params)  # 与上文同一份拼串实现
    pem = ("-----BEGIN PUBLIC KEY-----\n"
           + "\n".join(public_key_b64[i:i+64] for i in range(0, len(public_key_b64), 64))
           + "\n-----END PUBLIC KEY-----")
    public_key = serialization.load_pem_public_key(pem.encode())
    try:
        public_key.verify(base64.b64decode(sign), content.encode("utf-8"),
                          padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False
```

PHP：

```php
function verifyRsaSign(array $params, string $publicKey, string $sign): bool {
    $pem = "-----BEGIN PUBLIC KEY-----\n"
         . chunk_split($publicKey, 64, "\n")
         . "-----END PUBLIC KEY-----";
    return openssl_verify(getSignContent($params), base64_decode($sign),
                          $pem, OPENSSL_ALGO_SHA256) === 1;
}
```

Node.js：

```javascript
const crypto = require("crypto")

function verifyRsaSign(params, publicKeyB64, sign) {
  const pem = `-----BEGIN PUBLIC KEY-----\n${publicKeyB64.replace(/(.{64})/g, "$1\n")}-----END PUBLIC KEY-----`
  return crypto.createVerify("RSA-SHA256")
    .update(getSignContent(params), "utf8")
    .verify(pem, sign, "base64")
}
```

## 常见错误

| 错误信息 | 原因 |
|---|---|
| 缺少sign_type参数 | 请求未带 `sign_type`（必填） |
| 不支持的签名类型，仅支持 MD5 / RSA2 | `sign_type` 取值不是这两个之一 |
| 商户未开启RSA2签名，请先在商户面板开启并配置公钥 | 传了 `RSA2`，但商户后台未开启 |
| 商户未配置RSA公钥，请先在商户面板生成密钥 | 已开启 RSA2 但公钥为空 |
| 商户未配置MD5密钥，请先在商户面板生成 | 密钥被清空（正常不会出现，面板可重新生成） |
| MD5签名验证失败 / RSA签名验证失败 | 拼串、密钥或签名值不一致，先用「签名自检」比对 |
| 缺少timestamp参数 / 时间戳字段不正确 | 未传时间戳或与服务器偏差超过 300 秒 |
| 请求重复提交（签名已被使用），请重新签名 | 同一份请求 5 分钟内重复发送，刷新 `timestamp` 重签即可 |
