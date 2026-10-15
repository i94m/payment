"""启动授权门禁 — Ed25519 签名授权码校验（仅编译镜像强制，源码运行跳过）。

设计要点：
- 授权码由作者用离线私钥签发（scripts/license_gen.py，私钥只存作者本机），
  本模块只内置公钥，校验纯离线（签名 + 有效期），不访问任何网络
- 强制范围：仅本模块自身 __file__ 后缀非 .py（即镜像内 Cython 编译产物）时
  强制；本地源码运行 / pytest 一律跳过 —— 环境变量无法把 .so 伪装回 .py
- 在 config 导入期执行，此刻日志系统尚未初始化，失败信息直接写 stderr
- 授权码格式：PAY1.<b64url(payload_json)>.<b64url(ed25519签名)>，
  payload 形如 {"exp":<unix秒>,"iat":<unix秒>,"sub":"<客户备注>"}，
  签名覆盖 payload 段的原始 ASCII 字节
- 公钥必须编译进本模块（PUBLIC_KEY_B64），严禁改为从环境变量/.env 读取
  （可被替换即等于无校验）
"""
import base64
import json
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# 授权码前缀（换算法/换钥时递增，旧码自然失效）
CODE_PREFIX = "PAY1"

# ← 作者把 scripts/license_gen.py keygen 输出的公钥粘贴到这里（32 字节
#   原始公钥的标准 base64，44 字符）。留空时镜像内启动直接拒绝（fail-secure）。
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
    """惰性解析内置公钥（空/坏 → LicenseKeyError，绝不静默跳过校验）。"""
    if not PUBLIC_KEY_B64:
        raise LicenseKeyError("未内置授权公钥（PUBLIC_KEY_B64 为空）")
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64))
    except Exception:
        raise LicenseKeyError("内置授权公钥格式不正确") from None


def verify(code: str) -> dict:
    """校验授权码，返回 payload；失败抛 LicenseError 子类。"""
    global current
    code = (code or "").strip()
    if not code:
        raise LicenseFormatError("授权码为空")
    parts = code.split(".")
    if len(parts) != 3 or parts[0] != CODE_PREFIX:
        raise LicenseFormatError(f"格式不正确（应为 {CODE_PREFIX}.载荷.签名 三段）")
    payload_seg, sig_seg = parts[1], parts[2]
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
