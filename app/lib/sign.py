"""签名工具 — MD5（默认）与 RSA2（SHA256WithRSA / PKCS1v15）双算法。

两种算法共用同一份签名原文（get_sign_content）：参数类型先经 canon_params 归一，
服务端验签与文档示例口径一致，避免 `pid=1001` 与 `pid="1001"`、`money=1` 与
`money=1.00` 拼出不同原文导致对接排障。
"""
import base64
import hashlib
import hmac
from decimal import Decimal, InvalidOperation

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# 金额类参数：签名原文统一两位小数（客户端按字符串传、文档示例一律带引号）
_MONEY_FIELDS = ("money", "refund_amount")


def _is_empty(v) -> bool:
    """判断参数是否为空：仅 None 与空串（含纯空白）视为空。

    注意 "0" / 0 不算空，param=0 等值必须参与签名，否则与商户 SDK 的拼串验签不一致。
    """
    if v is None:
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _canon_value(key: str, value) -> str:
    """单个参数值的规范化字符串（签名原文用）。

    - 标量统一 str()：JSON 数字 1001 与字符串 "1001" 拼串一致
    - money / refund_amount 统一两位小数：1 / "1" / 1.0 / "1.0" 均为 "1.00"
    """
    if key in _MONEY_FIELDS:
        try:
            return f"{Decimal(str(value)).quantize(Decimal('0.01')):f}"
        except (InvalidOperation, ValueError, TypeError):
            pass
    return str(value)


def canon_params(data: dict) -> dict:
    """签名参数规范化：排除 sign/sign_type、空值与数组/字典，标量转字符串。"""
    return {
        k: _canon_value(k, data[k])
        for k in data
        if k not in ("sign", "sign_type")
        and not _is_empty(data[k])
        and not isinstance(data[k], (list, dict))
    }


def get_sign_content(data: dict) -> str:
    """构建签名原文：按 key 排序，排除 sign/sign_type、空值、数组/字典"""
    canon = canon_params(data)
    return "&".join(f"{k}={canon[k]}" for k in sorted(canon))


def base64_to_pem(data: str, key_type: str = "PUBLIC KEY") -> str:
    """将原始 base64 密钥转换为 PEM 格式（对齐 PHP base64ToPem）"""
    if not data or "-----BEGIN" in data:
        return data
    lines = [data[i:i+64] for i in range(0, len(data), 64)]
    return f"-----BEGIN {key_type}-----\n" + "\n".join(lines) + f"\n-----END {key_type}-----"


def normalize_key(raw: str) -> str:
    """清洗后台粘贴的密钥（textarea 直接贴多行 PEM / 带换行的 base64）。

    - 统一换行符、去掉首尾空白：粘贴时最常见的 \r\n 与尾随空行
    - 带 PEM 头尾的保持多行原样（load_pem_* 能解析）
    - 无头尾的压成单行 base64，避免换行被当成 base64 字符导致解析失败
    """
    text = (raw or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if "-----BEGIN" in text:
        return text
    return "".join(text.split())


def pem_to_flat(data: str) -> str:
    """将 PEM 密钥转为无头尾的单行 base64（对齐 PHP pemToBase64 的老系统观感）。

    非 PEM 输入原样返回；base64_to_pem 的逆操作，两者配合实现双格式兼容。
    """
    if not data or "-----BEGIN" not in data:
        return data or ""
    return "".join(line.strip() for line in data.splitlines() if "-----" not in line)


def derive_public_key(private_key_b64: str) -> str:
    """由 RSA 私钥（单行 base64 或 PEM 均可）派生公钥，返回无头尾单行 base64。

    平台公钥不落库，统一由 platform_rsa_private_key 实时派生，保证公私钥配对。
    私钥为空返回空串；解析失败抛 ValueError（调用方决定提示口径）。
    """
    raw = (private_key_b64 or "").strip()
    if not raw:
        return ""
    try:
        private_key = _load_private_key(raw)
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")
    except Exception as e:
        raise ValueError("RSA私钥解析失败") from e
    return pem_to_flat(public_pem)


# 已解析密钥对象缓存：PEM 解析昂贵（RSA-2048 实测 ~40ms/次），而签名/验签都在
# 请求路径上 —— 逐次解析的同步 CPU 会阻塞事件循环，单 worker 下单被压到 ~25 rps。
# 密钥内容不变，按原始串缓存进程内解析结果（key 轮换=新串=新缓存项，旧项自然淘汰不了
# 但轮换极低频，进程内常驻可接受）。
_private_key_cache: dict[str, object] = {}
_public_key_cache: dict[str, object] = {}


def _load_private_key(raw: str):
    raw = normalize_key(raw)
    cached = _private_key_cache.get(raw)
    if cached is not None:
        return cached
    pem = base64_to_pem(raw, "PRIVATE KEY")
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    _private_key_cache[raw] = key
    return key


def _load_public_key(raw: str):
    raw = normalize_key(raw)
    cached = _public_key_cache.get(raw)
    if cached is not None:
        return cached
    pem = base64_to_pem(raw, "PUBLIC KEY")
    key = serialization.load_pem_public_key(pem.encode())
    _public_key_cache[raw] = key
    return key


def make_rsa_sign(data: dict, private_key_pem: str) -> str:
    """RSA2 签名（SHA256WithRSA / PKCS1v15），返回 base64"""
    sign_str = get_sign_content(data)
    private_key = _load_private_key(private_key_pem)
    signature = private_key.sign(sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode("utf-8")


def verify_rsa_sign(data: dict, public_key_b64: str, sign: str) -> bool:
    """RSA2 验签 — 自动将 raw base64 转为 PEM"""
    sign_str = get_sign_content(data)
    try:
        public_key = _load_public_key(public_key_b64)
        public_key.verify(base64.b64decode(sign), sign_str.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def make_md5_sign(data: dict, secret: str) -> str:
    """MD5 签名：md5(签名原文 + 密钥) → 32 位小写 hex（易支付口径，密钥做后缀）。"""
    raw = get_sign_content(data) + (secret or "")
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify_md5_sign(data: dict, secret: str, sign: str) -> bool:
    """MD5 验签（常量时间比较；大小写不敏感）。"""
    if not secret or not sign:
        return False
    expect = make_md5_sign(data, secret)
    return hmac.compare_digest(expect, str(sign).strip().lower())
