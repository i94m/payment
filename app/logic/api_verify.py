"""下游商户开放 API 签名校验（RSA2 单算法）。

- 签名在 JSON body 参数内：sign（base64 RSA2）+ sign_type 固定 RSA2
- timestamp 必填，服务端 |now - ts| <= 300s 防重放
- 签名原文：get_sign_content（key 升序、排除 sign/sign_type/空值/数组/字典）
"""
import time

from app.lib.sign import verify_rsa_sign


def verify_api_signature(public_key: str | None, params: dict) -> None:
    """校验商户请求 RSA2 签名。失败抛 ValueError。

    - public_key 未配置 → 明确引导商户面板一键生成密钥对
    - sign / timestamp 缺一不可；时间窗口 ±300s
    """
    sign = str(params.get("sign", "") or "")
    if not sign:
        raise ValueError("签名不能为空")

    if not public_key:
        raise ValueError("商户未配置RSA公钥，请先在商户面板生成密钥")

    timestamp = str(params.get("timestamp", "") or "")
    if not timestamp:
        raise ValueError("缺少timestamp参数")
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        raise ValueError("时间戳格式错误")
    if abs(time.time() - ts) > 300:
        raise ValueError("时间戳字段不正确，请检查服务器时间")

    if not verify_rsa_sign(params, public_key, sign):
        raise ValueError("RSA签名验证失败")
