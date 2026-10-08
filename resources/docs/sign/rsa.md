# RSA2签名算法

平台开放 API 统一使用 **RSA2（SHA256WithRSA / PKCS#1 v1.5）** 单一签名算法：
签名放在 **JSON body 参数内**（支付宝风格）——拼参数 → 商户私钥签名 → POST JSON，
无需 Authorization 头。

- 签名原文：全部请求参数按参数名 ASCII 升序拼成 `a=b&c=d&e=f`（参数值不做 URL 编码），
  其中 `sign`、`sign_type` 与空值（`null`、空字符串）**不参与**，字符串 `"0"` **参与**签名
- 签名算法：SHA256WithRSA（PKCS#1 v1.5），结果 Base64 编码后作为 `sign` 参数
- `sign_type` 固定传 `RSA2`
- `timestamp` 必填：Unix 秒时间戳，与服务器偏差超过 300 秒拒绝（防重放）
- 密钥规格：RSA-2048 / PKCS#8；公钥格式为原始单行 Base64 或 PEM 均可

同一套规则用于两个方向：

| 方向 | 签名密钥 | 验签密钥 |
|---|---|---|
| 商户 → 平台（下单/查单/退款） | 商户私钥 | 商户公钥（平台存管） |
| 平台 → 商户（异步通知） | 平台私钥 | 平台公钥（商户面板查看） |

## 签名四步

1. 去掉 `sign`、`sign_type`，去掉空值参数；
2. 其余参数按参数名 ASCII 升序排序，拼成 `k1=v1&k2=v2&...`（值不 URL 编码）；
3. 用商户私钥对拼串做 SHA256WithRSA 签名，结果 Base64 编码得到 `sign`；
4. 请求 JSON body 中带上 `sign`、`sign_type=RSA2` 与 `timestamp`。

### 签名示例

待签名参数：

```json
{
  "money": "1.00",
  "name": "VIP会员",
  "notify_url": "http://www.pay.com/notify_url.php",
  "out_trade_no": "20160806151343349",
  "pid": 1001,
  "timestamp": 1696000000,
  "type": "alipay"
}
```

排序拼串后的签名原文：

```text
money=1.00&name=VIP会员&notify_url=http://www.pay.com/notify_url.php&out_trade_no=20160806151343349&pid=1001&timestamp=1696000000&type=alipay
```

## 如何获取密钥对

商户后台「安全设置 → 接口密钥」点击**一键生成密钥对**：平台自动生成 RSA-2048 密钥对，
公钥直接保存到商户资料，**私钥仅此一次展示**——请立即复制并妥善保管（关掉弹窗后无法再次查看）。

## 签名代码示例

Python：

```python
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

def make_rsa_sign(params: dict, private_key_pem: str) -> str:
    keys = sorted(
        k for k in params
        if k not in ("sign", "sign_type") and params[k] not in ("", None)
    )
    sign_str = "&".join(f"{k}={params[k]}" for k in keys)
    private_key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = private_key.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("utf-8")
```

PHP：

```php
function getSignContent($params) {
    ksort($params);
    $parts = [];
    foreach ($params as $k => $v) {
        if ($k === 'sign' || $k === 'sign_type' || $v === '' || $v === null) continue;
        $parts[] = $k . '=' . $v;
    }
    return implode('&', $parts);
}

openssl_sign(getSignContent($params), $signature, $privateKeyPem, OPENSSL_ALGO_SHA256);
$sign = base64_encode($signature);
```

Node.js：

```javascript
const crypto = require("crypto")

function makeRsaSign(params, privateKeyPem) {
  const keys = Object.keys(params)
    .filter((k) => !["sign", "sign_type"].includes(k) && params[k] !== "" && params[k] != null)
    .sort()
  const signStr = keys.map((k) => `${k}=${params[k]}`).join("&")
  return crypto.createSign("RSA-SHA256")
    .update(signStr, "utf8")
    .sign(privateKeyPem, "base64")
}
```

## 验签代码示例（平台公钥）

用于验证平台 [回调通知](/pay/notify.html)（POST JSON body 内签名）。平台公钥可在
商户后台「安全设置 → 接口密钥」查看复制（单行 Base64）。

Python：

```python
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

def verify_rsa_sign(params: dict, public_key_b64: str, sign: str) -> bool:
    keys = sorted(
        k for k in params
        if k not in ("sign", "sign_type") and params[k] not in ("", None)
    )
    sign_str = "&".join(f"{k}={params[k]}" for k in keys)
    pem = ("-----BEGIN PUBLIC KEY-----\n"
           + "\n".join(public_key_b64[i:i+64] for i in range(0, len(public_key_b64), 64))
           + "\n-----END PUBLIC KEY-----")
    public_key = serialization.load_pem_public_key(pem.encode())
    try:
        public_key.verify(base64.b64decode(sign), sign_str.encode("utf-8"),
                          padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False
```

PHP：

```php
function verifyRsaSign($params, $publicKey, $sign) {
    ksort($params);
    $parts = [];
    foreach ($params as $k => $v) {
        if ($k === 'sign' || $k === 'sign_type' || $v === '' || $v === null) continue;
        $parts[] = $k . '=' . $v;
    }
    $pem = "-----BEGIN PUBLIC KEY-----\n"
         . chunk_split($publicKey, 64, "\n")
         . "-----END PUBLIC KEY-----";
    return openssl_verify(implode('&', $parts), base64_decode($sign),
                          $pem, OPENSSL_ALGO_SHA256) === 1;
}
```

Node.js：

```javascript
const crypto = require("crypto")

function verifyRsaSign(params, publicKeyB64, sign) {
  const keys = Object.keys(params)
    .filter((k) => !["sign", "sign_type"].includes(k) && params[k] !== "" && params[k] != null)
    .sort()
  const signStr = keys.map((k) => `${k}=${params[k]}`).join("&")
  const pem = `-----BEGIN PUBLIC KEY-----\n${publicKeyB64.replace(/(.{64})/g, "$1\n")}-----END PUBLIC KEY-----`
  return crypto.createVerify("RSA-SHA256")
    .update(signStr, "utf8")
    .verify(pem, sign, "base64")
}
```

注：通知重试最长可达 2 小时，商户侧对通知 `timestamp` 的校验可按自身策略放宽；
验签请直接使用收到的参数原值拼串（通知中 `money` 固定两位小数，如 `5.00`）。
