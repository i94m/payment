"""启动授权门禁 — Ed25519 签名授权码校验（仅编译镜像强制，源码运行跳过）。

设计要点：
- 授权码由作者用离线私钥签发（scripts/license_gen.py，私钥只存作者本机），
  本模块内置默认公钥，校验纯离线（签名 + 有效期），不访问任何网络
- 强制范围：仅本模块自身 __file__ 后缀非 .py（即镜像内 Cython 编译产物）时
  强制；本地源码运行 / pytest 一律跳过 —— 环境变量无法把 .so 伪装回 .py
- 在 config 导入期执行，此刻日志系统尚未初始化，失败信息直接写 stderr
- 授权码为空/无效不退出进程：记录 startup_error，进入「未授权软锁」——
  Web 页面展示「无效的授权码」，业务请求由 main.py 中间件一律 503，
  /health 豁免供容器监控（页面可访问、业务全断，比启动失败更好排障）
- 授权码格式：<b64url(payload_json)>.<b64url(ed25519签名)>（两段），
  payload 形如 {"exp":<unix秒>,"iat":<unix秒>,"sub":"<客户备注>"}，
  签名覆盖 payload 段的原始 ASCII 字节；旧版码前的 "PAY1." 前缀兼容可验
- 公钥唯一来源：内置 PUBLIC_KEY_B64，随 Cython 编译进镜像 .so 的常量
  （不提供环境变量/配置覆盖 —— 不给部署方"换公钥 + 自签授权码"留口子；
  换钥 = 改此处后重建并重发镜像）。内置为空/格式非法属镜像构建问题
  （部署方无法自修），仍 LicenseKeyError 退出（fail-secure，绝不静默跳过校验）
"""
import base64
import json
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# 旧版授权码前缀（v1 码形如 PAY1.载荷.签名；新签发的码不再带前缀，校验兼容两种）
CODE_PREFIX = "PAY1"

# ← 内置默认公钥（scripts/license_gen.py keygen/pubkey 输出的 32 字节原始
#   公钥的标准 base64，44 字符）。唯一公钥来源：编译进镜像 .so，无配置覆盖；
#   换钥 = 改此处后重建镜像并让各部署重新拉取。留空/非法时镜像内启动直接
#   拒绝（fail-secure）。
PUBLIC_KEY_B64: str = "+pQsryoUV5Zhf6zMPBzT3jXjglZg2M5aMqVO3belhSw="

# 最近一次校验成功的 payload（main.py lifespan / worker 启动日志展示用）
current: dict | None = None

# 启动校验失败原因（仅编译镜像且码为空/无效时置位；None=授权有效或源码运行）。
# 置位后进程不退出，main.py 软锁中间件据 lock_reason() 逐请求拦截。
startup_error: str | None = None


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
        raise LicenseFormatError("Invalid license code encoding") from None


def _load_public_key() -> Ed25519PublicKey:
    """惰性解析内置公钥。空/坏 → LicenseKeyError，绝不静默跳过校验。"""
    key_b64 = PUBLIC_KEY_B64.strip()
    if not key_b64:
        raise LicenseKeyError("License public key not configured (built-in PUBLIC_KEY_B64 is empty)")
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
    except Exception:
        raise LicenseKeyError("Invalid license public key format (built-in PUBLIC_KEY_B64 must be standard base64 of a 32-byte raw key)") from None


def verify(code: str) -> dict:
    """校验授权码，返回 payload；失败抛 LicenseError 子类。

    接受两段裸码（载荷.签名）；旧版三段码（PAY1.载荷.签名）兼容可验。
    """
    global current
    code = (code or "").strip()
    if not code:
        raise LicenseFormatError("License code is empty")
    parts = code.split(".")
    if parts[0] == CODE_PREFIX:
        parts = parts[1:]
    if len(parts) != 2:
        raise LicenseFormatError("Invalid format (expected payload.signature; legacy PAY1.-prefixed codes are also accepted)")
    payload_seg, sig_seg = parts
    payload_bytes = _b64url_decode(payload_seg)
    sig = _b64url_decode(sig_seg)
    try:
        _load_public_key().verify(sig, payload_seg.encode("ascii"))
    except LicenseError:
        raise
    except Exception:
        raise LicenseSignatureError("Invalid signature") from None
    try:
        payload = json.loads(payload_bytes)
    except Exception:
        raise LicenseFormatError("Payload is not valid JSON") from None
    if not isinstance(payload, dict):
        raise LicenseFormatError("Payload must be a JSON object")
    exp = payload.get("exp")
    # bool 是 int 子类，需排除；未知扩展键忽略（前向兼容）
    if isinstance(exp, bool) or not isinstance(exp, int):
        raise LicenseFormatError("Payload is missing expiration exp (unix seconds)")
    if exp <= time.time():
        raise LicenseExpiredError(
            f"License expired at {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}")
    current = payload
    return payload


def enforce(code: str) -> None:
    """启动门禁：源码运行直接放行；编译镜像校验失败不退出，转未授权软锁。

    码为空/无效：记录 startup_error（lock_reason 据此软锁，页面展示
    「无效的授权码」，业务请求 503），容器保持运行便于排障；
    内置公钥缺失/损坏属镜像构建问题（部署方无法自修），仍写 stderr 退出。

    config 导入期调用（此刻 app 日志尚未初始化），所有入口
    （uvicorn / SAQ worker / 容器内 artisan、alembic）统一经此拦截。
    """
    global startup_error
    startup_error = None
    if not _is_compiled():
        return  # 本地源码运行 / pytest：不校验
    try:
        verify(code)
    except LicenseKeyError as e:
        sys.stderr.write(
            "\n"
            "==========================================================\n"
            "Startup failed: license public key unavailable (image build issue, fail-secure exit)\n"
            f"Reason: {e}\n"
            "Fix: the author must correct PUBLIC_KEY_B64, rebuild and redistribute the image\n"
            "==========================================================\n"
        )
        sys.stderr.flush()
        raise SystemExit(1) from None
    except LicenseError as e:
        startup_error = f"Invalid license code: {e}"
        sys.stderr.write(
            "\n"
            "==========================================================\n"
            "License verification failed; the service will start unlicensed (process stays up)\n"
            f"Reason: {e}\n"
            f"Current AUTH_CODE: {(code or '').strip()[:12] or '(empty)'}\n"
            "Fix: obtain a valid license code, set AUTH_CODE= in .env, then restart\n"
            "     the container (docker compose up -d). Verification is fully offline\n"
            "     and never accesses the network\n"
            "While unlicensed: web pages show the invalid-license notice, all\n"
            "business APIs return 503\n"
            "==========================================================\n"
        )
        sys.stderr.flush()


def lock_reason() -> str | None:
    """运行时软锁判断：未授权原因（None=放行）。

    两种来源：启动校验失败（startup_error，空码/坏码）或已加载授权
    过期（逐请求纯内存时间比对，无加密运算）。供 main.py 请求中间件
    使用；源码运行两者皆不成立恒为 None，本地开发不受影响。
    """
    if startup_error:
        return startup_error
    exp = (current or {}).get("exp")
    if isinstance(exp, int) and not isinstance(exp, bool) and exp <= time.time():
        return f"License expired at {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}, please renew it with the author and restart the service"
    return None


def describe() -> str:
    """已加载授权的可读描述（启动日志展示用；未加载时给出提示语）。"""
    if not current:
        return "未加载授权信息"
    sub = current.get("sub") or "默认"
    exp = current.get("exp")
    if isinstance(exp, int) and not isinstance(exp, bool):
        return f"授权对象：{sub}；有效期至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}"
    return f"授权对象：{sub}"


def expire_date() -> str | None:
    """已加载授权的到期日期（YYYY-MM-DD，本地时区）；未加载返回 None。

    供公开端点 /license/expire 使用：只输出日期，不泄露授权对象等其他信息。
    """
    exp = (current or {}).get("exp")
    if isinstance(exp, int) and not isinstance(exp, bool):
        return time.strftime("%Y-%m-%d", time.localtime(exp))
    return None
