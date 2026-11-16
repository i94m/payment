"""下游商户开放 API 签名校验（RSA2 单算法）。

- 签名在 JSON body 参数内：sign（base64 RSA2）+ sign_type 固定 RSA2
- timestamp 必填，服务端 |now - ts| <= 300s 防重放
- 签名原文：get_sign_content（key 升序、排除 sign/sign_type/空值/数组/字典）
- 一次性校验：同一份签名在 300s 窗口内只受理一次（Redis SET NX），
  否则攻击者截获明文请求即可在窗口内重复调用（退款接口在你没传 out_refund_no
  时会重复扣商户余额）。时间窗只能限制「多久内可重放」，拦不住「窗口内重放」。
"""
import hashlib
import time

from app.lib.sign import verify_rsa_sign

# 与 timestamp 时间窗一致的签名一次性缓存时长（秒）
REPLAY_TTL = 300


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


def replay_key(merchant_id: int, sign: str) -> str:
    """签名一次性键：商户 + 签名摘要（签名覆盖全部业务参数，摘要有业务唯一性）。"""
    digest = hashlib.sha256(sign.encode("utf-8")).hexdigest()[:32]
    return f"apireplay:{merchant_id}:{digest}"


async def ensure_not_replayed(cache, merchant_id: int, params: dict) -> None:
    """签名一次性校验（Redis SET NX EX REPLAY_TTL）。重复请求抛 ValueError。

    cache 为空（未接入 Redis 的本地/测试链路）时跳过：此时只剩 ±300s 时间窗约束，
    与改造前行为一致；生产环境 cache 一定存在。
    """
    sign = str(params.get("sign", "") or "")
    if not sign or cache is None:
        return
    got = await cache.set(replay_key(merchant_id, sign), "1", nx=True, ex=REPLAY_TTL)
    if not got:
        raise ValueError("请求重复提交（签名已被使用），请重新签名")
