"""授权码校验（app/core/license.py）：格式 / 签名 / 有效期 / 强制分支。

用测试密钥对现签现验（monkeypatch 内置公钥常量）；编译镜像分支通过
改写模块 __file__ 后缀模拟（源码/编译两种形态该属性均可写），无需 docker。
"""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import license as lic

# 模块级测试密钥对（只用于本文件签名/验签）
_priv = Ed25519PrivateKey.generate()
TEST_PUB_B64 = base64.b64encode(_priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()

# 模拟镜像内编译产物的模块路径
SO_FILE = "/app/app/core/license.cpython-312-x86_64-linux-gnu.so"


@pytest.fixture(autouse=True)
def _patch_key(monkeypatch):
    """每个用例都换成测试公钥并清空已加载授权，避免串扰。"""
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", TEST_PUB_B64)
    monkeypatch.setattr(lic, "current", None)


def _seg(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_code(payload: dict | None = None, *, key=_priv, prefix: str = "PAY1",
              tamper: bool = False) -> str:
    """按正式格式构造授权码；tamper 篡改载荷首字符（保持可解码、破坏签名）。"""
    if payload is None:
        payload = {"exp": int(time.time()) + 3600, "iat": int(time.time()), "sub": "测试客户"}
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    seg = _seg(payload_json)
    sig = key.sign(seg.encode("ascii"))
    if tamper:
        # 先签后改：模拟内容被篡改（保持可解码、破坏签名匹配）
        seg = ("B" if seg[0] != "B" else "C") + seg[1:]
    return f"{prefix}.{seg}.{_seg(sig)}"


# ============================================================
# verify：合法与格式
# ============================================================

async def test_verify_valid_code():
    payload = lic.verify(make_code())
    assert payload["sub"] == "测试客户"
    assert lic.current == payload


async def test_verify_ignores_unknown_keys():
    """未知扩展键忽略（前向兼容）。"""
    payload = make_code({"exp": int(time.time()) + 60, "sub": "x", "edition": "pro"})
    assert lic.verify(payload)["sub"] == "x"


@pytest.mark.parametrize("code", [
    "",                       # 空
    "garbage",                # 无分隔
    "PAY2.a.b",               # 前缀不对
    "PAY1.a",                 # 段数不够
    "PAY1.a.b.c",             # 段数过多
    "PAY1.!!!.!!!",           # 非 base64url
])
async def test_verify_bad_format(code):
    with pytest.raises(lic.LicenseFormatError):
        lic.verify(code)


async def test_verify_bad_json():
    import base64 as b64
    seg = _seg(b"not-json")
    sig = _seg(_priv.sign(seg.encode()))
    with pytest.raises(lic.LicenseFormatError):
        lic.verify(f"PAY1.{seg}.{sig}")


@pytest.mark.parametrize("payload", [
    {"sub": "x"},              # 缺 exp
    {"exp": "123"},            # exp 非整数
    {"exp": True},             # bool 是 int 子类，需排除
])
async def test_verify_bad_exp(payload):
    with pytest.raises(lic.LicenseFormatError):
        lic.verify(make_code(payload))


# ============================================================
# verify：签名与有效期
# ============================================================

async def test_verify_tampered_payload():
    with pytest.raises(lic.LicenseSignatureError):
        lic.verify(make_code(tamper=True))


async def test_verify_wrong_signing_key():
    with pytest.raises(lic.LicenseSignatureError):
        lic.verify(make_code(key=Ed25519PrivateKey.generate()))


async def test_verify_expired():
    with pytest.raises(lic.LicenseExpiredError):
        lic.verify(make_code({"exp": int(time.time()) - 3600}))


async def test_verify_exp_boundary_is_expired():
    """exp == now 算过期（闭区间边界，宁可提前拦截）。"""
    with pytest.raises(lic.LicenseExpiredError):
        lic.verify(make_code({"exp": int(time.time())}))


# ============================================================
# enforce：源码跳过 / 编译强制
# ============================================================

async def test_source_run_skips_enforce():
    """源码运行（默认 __file__ 为 .py）：即使空码也直接放行。"""
    assert lic.enforce("") is None


async def test_enforce_compiled_empty_code_exits(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    with pytest.raises(SystemExit) as ei:
        lic.enforce("")
    assert ei.value.code == 1
    assert "授权" in capsys.readouterr().err


async def test_enforce_compiled_bad_sig_exits(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    with pytest.raises(SystemExit):
        lic.enforce(make_code(tamper=True))
    assert "签名" in capsys.readouterr().err


async def test_enforce_compiled_expired_exits(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    with pytest.raises(SystemExit):
        lic.enforce(make_code({"exp": int(time.time()) - 10}))
    assert "过期" in capsys.readouterr().err


async def test_enforce_compiled_valid_passes(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    lic.enforce(make_code())
    assert lic.current is not None
    assert capsys.readouterr().err == ""


async def test_empty_baked_key_fails_secure(monkeypatch, capsys):
    """内置公钥为空（镜像构建遗漏）：拒绝启动而非跳过校验。"""
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", "")
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    with pytest.raises(SystemExit):
        lic.enforce(make_code())
    assert "公钥" in capsys.readouterr().err


# ============================================================
# expired（运行时软锁）与请求中间件
# ============================================================

async def test_expired_flags(monkeypatch):
    """未加载（源码运行）恒 False；过期布尔排除、边界为过期。"""
    assert lic.expired() is False
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) + 3600})
    assert lic.expired() is False
    monkeypatch.setattr(lic, "current", {"exp": int(time.time())})
    assert lic.expired() is True
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) - 1})
    assert lic.expired() is True
    monkeypatch.setattr(lic, "current", {"sub": "no-exp"})
    assert lic.expired() is False


async def test_soft_lock_middleware(client, monkeypatch):
    """过期后业务 API 即刻 503，/health 豁免保持监控可用。"""
    # 未过期（current 为 None）：放行
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/admin-api/pay/channels")).status_code != 503
    # 过期：业务路径断流，健康检查照常
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) - 1})
    r = await client.get("/admin-api/pay/channels")
    assert r.status_code == 503
    assert "过期" in r.json()["message"]
    assert (await client.get("/health")).status_code == 200


# ============================================================
# describe
# ============================================================

async def test_describe_contains_note_and_expiry():
    lic.verify(make_code({"exp": int(time.time()) + 3600, "sub": "客户A"}))
    text = lic.describe()
    assert "客户A" in text
    assert "有效期至" in text


async def test_describe_before_load():
    assert lic.describe() == "未加载授权信息"
