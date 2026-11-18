"""插件包签名校验 —— Ed25519 签名（三方插件安装 / 加载的门禁）。

为什么需要：**后台上传**安装插件 = 在应用进程里 `exec_module` 一个 .so，等同于任意代码
执行。本模块把「上传的包必须由作者私钥签名」变成硬性条件，避免账号被盗或供应商投毒。

边界：只在「上传安装」这一步校验（`admin/channel.py::plugin_upload`）。部署方直接
把编译好的插件目录拷进 `storage/plugins/` 时**不校验**——能操作服务器文件本身就是
最高权限，默认信任。

签名口径（与 scripts/plugin_sign.py 一一对应）：
- payload = {"name": <插件名>, "sha256": <plugin*.so 的 sha256>, "version": <可选>, "iat": <unix秒>}
- 待签串 = "PLUGIN1:" + base64url(payload_json)（ASCII）—— 与授权码同构：只签编码后的载荷段，
  前缀做用途域分隔，防止拿 AUTH_CODE 的签名冒充插件包签名
- 包内结构：<插件名>/plugin*.so + <插件名>/signature.json
  signature.json = {"payload": <b64url>, "sig": <b64url>}

强制范围：编译镜像（本模块 __file__ 后缀非 .py）强制；源码运行 / pytest 跳过，
本地开发不必签包；也可用环境变量 PLUGIN_SIGN_REQUIRED=1 在源码环境打开。编译镜像内
无法关闭（不给部署方「关掉校验绕过去」留口子）。

公钥唯一来源：内置 PLUGIN_PUBLIC_KEY_B64（随 Cython 编译进镜像 .so，无配置覆盖；
换钥 = 改此处 + 重建镜像 + 旧包重签）。
"""
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# 签名载荷前缀（域分隔）：插件包签名专用
PREFIX = "PLUGIN1"

# 包内签名文件名（与 .so 同级）
SIGN_FILE = "signature.json"

# ← 内置公钥（与授权码同一把 Ed25519 私钥对应的 32 字节原始公钥 base64）。
#   私钥 license_private.pem 只在作者本机；换钥需重建镜像并重签所有待分发插件包。
PLUGIN_PUBLIC_KEY_B64: str = "+pQsryoUV5Zhf6zMPBzT3jXjglZg2M5aMqVO3belhSw="


class PackageSignatureError(Exception):
    """插件包签名缺失 / 格式错误 / 摘要或名字不符。"""


def _is_compiled() -> bool:
    """是否以编译形态运行（.so）：只有 .py（源码）跳过强制校验。"""
    return Path(__file__).suffix != ".py"


def is_enforced() -> bool:
    """当前进程是否强制校验插件包签名。"""
    if _is_compiled():
        return True
    return os.getenv("PLUGIN_SIGN_REQUIRED", "").strip().lower() in ("1", "true", "yes")


# ============================================================
# 编解码
# ============================================================

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(seg: str) -> bytes:
    try:
        std = (seg or "").replace("-", "+").replace("_", "/")
        return base64.b64decode(std + "=" * (-len(seg) % 4), validate=True)
    except Exception:
        raise PackageSignatureError("签名载荷编码不合法") from None


def _load_public_key(public_key_b64: Optional[str] = None) -> Ed25519PublicKey:
    key_b64 = (public_key_b64 if public_key_b64 is not None else PLUGIN_PUBLIC_KEY_B64).strip()
    if not key_b64:
        raise PackageSignatureError("插件签名公钥未配置（内置 PLUGIN_PUBLIC_KEY_B64 为空）")
    try:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64, validate=True))
    except Exception:
        raise PackageSignatureError("插件签名公钥格式非法（应为 32 字节原始公钥的标准 base64）") from None


# ============================================================
# 载荷构造 / 签名（供 scripts/plugin_sign.py 与测试使用；本模块不加载私钥）
# ============================================================

def build_payload(name: str, sha256: str, version: str = "", iat: Optional[int] = None) -> dict:
    """规范化 payload：只放校验必需项 + 审计信息。"""
    payload: dict[str, Any] = {
        "name": str(name or "").strip(),
        "sha256": str(sha256 or "").strip().lower(),
        "iat": int(iat if iat is not None else time.time()),
    }
    if version:
        payload["version"] = str(version)
    return payload


def _payload_seg(payload: dict) -> str:
    """载荷 → base64url 段（紧凑 JSON，键顺序即插入顺序，签名/验签都按这一段算）。"""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return _b64url_encode(raw)


def sign_payload(payload: dict, private_key: Ed25519PrivateKey) -> dict:
    """签 payload，返回可落盘的 {"payload": <b64url>, "sig": <b64url>}。"""
    seg = _payload_seg(payload)
    sig = private_key.sign(f"{PREFIX}:{seg}".encode("ascii"))
    return {"payload": seg, "sig": _b64url_encode(sig)}


def verify_signature_doc(doc: Any, public_key_b64: Optional[str] = None) -> dict:
    """验 signature.json 结构（载荷段 + 签名），返回 payload；失败抛 PackageSignatureError。"""
    if not isinstance(doc, dict):
        raise PackageSignatureError("签名文件不是 JSON 对象")
    payload_seg = str(doc.get("payload") or "").strip()
    sig_seg = str(doc.get("sig") or "").strip()
    if not payload_seg or not sig_seg:
        raise PackageSignatureError("签名文件缺少 payload / sig")

    sig = _b64url_decode(sig_seg)
    key = _load_public_key(public_key_b64)
    try:
        key.verify(sig, f"{PREFIX}:{payload_seg}".encode("ascii"))
    except Exception:
        raise PackageSignatureError("签名无效（包被改动或非我方签发）") from None

    try:
        payload = json.loads(_b64url_decode(payload_seg))
    except PackageSignatureError:
        raise
    except Exception:
        raise PackageSignatureError("签名载荷不是合法 JSON") from None
    if not isinstance(payload, dict):
        raise PackageSignatureError("签名载荷必须是 JSON 对象")
    return payload


# ============================================================
# 包级校验
# ============================================================

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_package(name: str, so_path: Path, doc: Any,
                   public_key_b64: Optional[str] = None) -> dict:
    """校验「签名 + 摘要 + 插件名」，返回 payload；任一不符抛 PackageSignatureError。"""
    payload = verify_signature_doc(doc, public_key_b64)

    signed_name = str(payload.get("name") or "").strip()
    if signed_name != str(name or "").strip():
        raise PackageSignatureError(f"签名里的插件名与目录名不一致（签名：{signed_name or '空'}）")

    signed_sha = str(payload.get("sha256") or "").strip().lower()
    if not signed_sha:
        raise PackageSignatureError("签名载荷缺少 sha256")
    actual_sha = sha256_file(so_path)
    if not hmac.compare_digest(signed_sha, actual_sha):
        raise PackageSignatureError("插件 .so 摘要与签名不一致（包被改动）")
    return payload


def verify_plugin_dir(plugin_dir: Path, so_path: Path, expect_name: Optional[str] = None,
                      public_key_b64: Optional[str] = None) -> dict:
    """校验插件目录（signature.json 与 .so 同级）。目录名默认取自目录。"""
    sign_path = Path(plugin_dir) / SIGN_FILE
    if not sign_path.is_file():
        raise PackageSignatureError(f"缺少 {SIGN_FILE}（未签名的插件包不予安装）")
    try:
        doc = json.loads(sign_path.read_text(encoding="utf-8"))
    except Exception:
        raise PackageSignatureError(f"{SIGN_FILE} 不是合法 JSON") from None
    name = expect_name or Path(plugin_dir).name
    return verify_package(name, Path(so_path), doc, public_key_b64)
