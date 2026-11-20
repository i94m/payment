"""下游商户开放 API 签名校验（同一套接口，同一时间只有一种算法生效）。

- `sign_type` 必填：`MD5` 或 `RSA2`，取值必须与商户当前开关一致
  - 未开启 RSA2（默认）：只接受 `MD5`（商户创建即生成密钥）
  - 已开启 RSA2：只接受 `RSA2`，MD5 立即失效（需先配置商户公钥）
- MD5：`md5(签名原文 + api_secret)`，32 位小写 hex（易支付口径，密钥后缀）
- RSA2：SHA256WithRSA + Base64，商户私钥签名 / 平台存管公钥验签
- 签名原文：get_sign_content（key 升序、排除 sign/sign_type/空值/数组/字典，
  money 等金额字段统一两位小数）
- timestamp 必填，|now - ts| <= 300s；同一份签名 300s 内只受理一次（Redis SET NX），
  否则攻击者截获明文请求即可在窗口内重复调用（退款接口会重复扣商户余额）。
"""
import hashlib
import time

from app.lib.sign import verify_md5_sign, verify_rsa_sign
from app.services.apikey import get_api_secret

# 与 timestamp 时间窗一致的签名一次性缓存时长（秒）
REPLAY_TTL = 300

# 支持的签名算法（对外只有这两值，大小写不敏感）
SIGN_TYPES = ("MD5", "RSA2")


def normalize_sign_type(raw) -> str:
    """sign_type 归一：去空白转大写；缺失/未知一律抛 ValueError（必填项）。"""
    value = str(raw or "").strip().upper()
    if not value:
        raise ValueError("缺少sign_type参数")
    if value not in SIGN_TYPES:
        raise ValueError("不支持的签名类型，仅支持 MD5 / RSA2")
    return value


def verify_api_signature(merchant, params: dict) -> str:
    """校验商户请求签名，返回本次使用的签名算法（MD5/RSA2）。失败抛 ValueError。

    算法由商户开关独占决定：未开启 RSA2 只接受 MD5，开启后只接受 RSA2。
    顺序：sign 非空 → sign_type 合法 → 算法与开关一致 → 凭据齐全 → timestamp → 验签。
    参数类错误先报，不做无意义的 RSA 运算。
    """
    sign = str(params.get("sign", "") or "")
    if not sign:
        raise ValueError("签名不能为空")

    sign_type = normalize_sign_type(params.get("sign_type"))

    secret = ""
    if merchant.rsa2_enable:
        if sign_type != "RSA2":
            raise ValueError("商户已启用RSA2签名，请使用sign_type=RSA2")
        if not merchant.public_key:
            raise ValueError("商户未配置RSA公钥，请先在商户面板生成密钥")
    else:
        if sign_type != "MD5":
            raise ValueError("商户未开启RSA2签名，请使用sign_type=MD5")
        secret = get_api_secret(merchant)
        if not secret:
            raise ValueError("商户未配置MD5密钥，请先在商户面板生成")

    timestamp = str(params.get("timestamp", "") or "")
    if not timestamp:
        raise ValueError("缺少timestamp参数")
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        raise ValueError("时间戳格式错误")
    if abs(time.time() - ts) > 300:
        raise ValueError("时间戳字段不正确，请检查服务器时间")

    if sign_type == "MD5":
        if not verify_md5_sign(params, secret, sign):
            raise ValueError("MD5签名验证失败")
    elif not verify_rsa_sign(params, merchant.public_key, sign):
        raise ValueError("RSA签名验证失败")
    return sign_type


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
