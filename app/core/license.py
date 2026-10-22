"""启动授权门禁 — Ed25519 签名授权码校验（仅编译镜像强制，源码运行跳过）。

设计要点：
- 授权码由作者用离线私钥签发（scripts/license_gen.py，私钥只存作者本机），
  本模块内置默认公钥，校验纯离线（签名 + 有效期），不访问任何网络
- 强制范围：仅本模块自身 __file__ 后缀非 .py（即镜像内 Cython 编译产物）时
  强制；本地源码运行 / pytest 一律跳过 —— 环境变量无法把 .so 伪装回 .py
- 在 config 导入期执行，此刻日志系统尚未初始化，失败信息直接写 stderr
- 授权码格式：<b64url(payload_json)>.<b64url(ed25519签名)>（两段），
  payload 形如 {"exp":<unix秒>,"iat":<unix秒>,"sub":"<客户备注>"}，
  签名覆盖 payload 段的原始 ASCII 字节；旧版码前的 "PAY1." 前缀兼容可验
- 公钥解析优先级：环境变量 LICENSE_PUBLIC_KEY（32 字节原始公钥的标准
  base64，44 字符纯字符串；docker-compose 的 environment: 或 env_file 注入
  均可）→ 未设置/留空时回退内置 PUBLIC_KEY_B64。两者都空或格式非法一律
  LicenseKeyError 拒绝启动（fail-secure，绝不静默跳过校验）
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# 旧版授权码前缀（v1 码形如 PAY1.载荷.签名；新签发的码不再带前缀，校验兼容两种）
CODE_PREFIX = "PAY1"

# 公钥环境变量名（覆盖内置公钥；compose environment: / .env 经 env_file 注入）
PUBLIC_KEY_ENV = "LICENSE_PUBLIC_KEY"

# ← 内置默认公钥（scripts/license_gen.py keygen/pubkey 输出的 32 字节原始
#   公钥的标准 base64，44 字符）。可被环境变量 LICENSE_PUBLIC_KEY 覆盖
#   （换钥免重建镜像）；两者都留空时镜像内启动直接拒绝（fail-secure）。
PUBLIC_KEY_B64: str = "+pQsryoUV5Zhf6zMPBzT3jXjglZg2M5aMqVO3belhSw="

# 最近一次校验成功的 payload（main.py lifespan / worker 启动日志展示用）
current: dict | None = None


class LicenseError(Exception):
    """授权校验失败基类。"""


class LicenseFormatError(LicenseError):
    """格式不正确（前缀/段数/编码/JSON/缺 exp）。"""


class LicenseSignatureError(LicenseError):
    """签名无效（内容被篡改或非本系统签发）。"""


class LicenseExpiredError(LicenseError):
    """已过有效期。"""


class LicenseKeyError(LicenseError):
    """内置公钥缺失或损坏（镜像构建问题，fail-secure 拒绝启动）。"""


def _is_compiled() -> bool:
    """是否以编译形态运行（.so）：只有 .py（源码）跳过校验，其余一律强制。"""
    return Path(__file__).suffix != ".py"


def _b64url_decode(seg: str) -> bytes:
    """无填充 base64url 解码（自动补齐 padding；严格模式，非法字符即格式错误）。"""
    try:
        std = seg.replace("-", "+").replace("_", "/")
        return base64.b64decode(std + "=" * (-len(seg) % 4), validate=True)
    except Exception:
        raise LicenseFormatError("授权码编码不正确") from None


def _load_public_key() -> Ed25519PublicKey:
    """惰性解析公钥：环境变量 LICENSE_PUBLIC_KEY 优先，回退内置默认。

    空/坏 → LicenseKeyError（指明是哪一路配置的问题），绝不静默跳过校验。
    """
    env_key = os.environ.get(PUBLIC_KEY_ENV, "").strip()
    key_b64 = env_key or PUBLIC_KEY_B64.strip()
    source = f"环境变量 {PUBLIC_KEY_ENV}" if env_key else "内置 PUBLIC_KEY_B64"
    if not key_b64:
        raise LicenseKeyError(f"授权公钥未配置（{source} 为空）")
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
    except Exception:
        raise LicenseKeyError(f"授权公钥格式不正确（{source}，应为 32 字节原始公钥的标准 base64）") from None


def verify(code: str) -> dict:
    """校验授权码，返回 payload；失败抛 LicenseError 子类。

    接受两段裸码（载荷.签名）；旧版三段码（PAY1.载荷.签名）兼容可验。
    """
    global current
    code = (code or "").strip()
    if not code:
        raise LicenseFormatError("授权码为空")
    parts = code.split(".")
    if parts[0] == CODE_PREFIX:
        parts = parts[1:]
    if len(parts) != 2:
        raise LicenseFormatError("格式不正确（应为 载荷.签名 两段；旧版 PAY1. 前缀码亦可）")
    payload_seg, sig_seg = parts
    payload_bytes = _b64url_decode(payload_seg)
    sig = _b64url_decode(sig_seg)
    try:
        _load_public_key().verify(sig, payload_seg.encode("ascii"))
    except LicenseError:
        raise
    except Exception:
        raise LicenseSignatureError("签名无效") from None
    try:
        payload = json.loads(payload_bytes)
    except Exception:
        raise LicenseFormatError("载荷不是合法 JSON") from None
    if not isinstance(payload, dict):
        raise LicenseFormatError("载荷必须是 JSON 对象")
    exp = payload.get("exp")
    # bool 是 int 子类，需排除；未知扩展键忽略（前向兼容）
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise LicenseFormatError("载荷缺少有效期 exp（unix 秒）")
    if exp <= time.time():
        raise LicenseExpiredError(
            f"授权已于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))} 过期")
    current = payload
    return payload


def enforce(code: str) -> None:
    """启动门禁：源码运行直接放行；编译镜像校验失败则写 stderr 并退出。

    config 导入期调用（此刻 app 日志尚未初始化），所有入口
    （uvicorn / SAQ worker / 容器内 artisan、alembic）统一经此拦截。
    """
    if not _is_compiled():
        return  # 本地源码运行 / pytest：不校验
    try:
        verify(code)
    except LicenseError as e:
        sys.stderr.write(
            "\n"
            "==========================================================\n"
            "启动失败：授权码校验未通过\n"
            f"原因：{e}\n"
            f"当前 AUTH_CODE：{(code or '').strip()[:12] or '（空）'}\n"
            "处理：获取有效授权码后填入 .env 的 AUTH_CODE=，重建容器\n"
            "     （docker compose up -d）。校验完全离线，不会访问网络\n"
            "==========================================================\n"
        )
        sys.stderr.flush()
        raise SystemExit(1) from None


def expired() -> bool:
    """运行时软锁判断：已加载的授权是否已过有效期。

    供 main.py 请求中间件逐请求比对（纯内存时间比较，无加密运算）；
    源码运行（current 未加载）恒为 False，本地开发不受影响。
    """
    exp = (current or {}).get("exp")
    return isinstance(exp, int) and not isinstance(exp, bool) and exp <= time.time()


def describe() -> str:
    """已加载授权的可读描述（启动日志展示用；未加载时给出提示语）。"""
    if not current:
        return "未加载授权信息"
    sub = current.get("sub") or "默认"
    exp = current.get("exp")
    if isinstance(exp, int) and not isinstance(exp, bool):
        return f"授权对象：{sub}；有效期至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}"
    return f"授权对象：{sub}"
