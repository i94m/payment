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
    """每个用例都换成测试公钥并清空已加载授权/启动失败态，避免串扰。"""
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", TEST_PUB_B64)
    monkeypatch.setattr(lic, "current", None)
    monkeypatch.setattr(lic, "startup_error", None)


def _seg(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_code(payload: dict | None = None, *, key=_priv, prefix: str = "",
              tamper: bool = False) -> str:
    """按正式格式构造授权码（默认裸码两段；prefix="PAY1" 构造旧版三段码）；
    tamper 篡改载荷首字符（保持可解码、破坏签名）。"""
    if payload is None:
        payload = {"exp": int(time.time()) + 3600, "iat": int(time.time()), "sub": "测试客户"}
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    seg = _seg(payload_json)
    sig = key.sign(seg.encode("ascii"))
    if tamper:
        # 先签后改：模拟内容被篡改（保持可解码、破坏签名匹配）
        seg = ("B" if seg[0] != "B" else "C") + seg[1:]
    body = f"{seg}.{_seg(sig)}"
    return f"{prefix}.{body}" if prefix else body


# ============================================================
# verify：合法与格式
# ============================================================

async def test_verify_valid_code():
    payload = lic.verify(make_code())
    assert payload["sub"] == "测试客户"
    assert lic.current == payload


async def test_verify_legacy_prefix_still_accepted():
    """旧版 PAY1. 三段码（历史已签发的）继续可验。"""
    assert lic.verify(make_code(prefix=lic.CODE_PREFIX))["sub"] == "测试客户"


async def test_verify_ignores_unknown_keys():
    """未知扩展键忽略（前向兼容）。"""
    payload = make_code({"exp": int(time.time()) + 60, "sub": "x", "edition": "pro"})
    assert lic.verify(payload)["sub"] == "x"


@pytest.mark.parametrize("code", [
    "",                       # 空
    "garbage",                # 无分隔
    "a.b.c",                  # 裸码段数过多
    "a",                      # 裸码段数不够
    "PAY2.a.b",               # 前缀不对
    "PAY1.a",                 # 旧版码段数不够
    "PAY1.a.b.c",             # 旧版码段数过多
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
# enforce：源码跳过 / 编译强制（失败不退出，转未授权软锁）
# ============================================================

async def test_source_run_skips_enforce():
    """源码运行（默认 __file__ 为 .py）：即使空码也直接放行。"""
    assert lic.enforce("") is None
    assert lic.startup_error is None


async def test_enforce_compiled_empty_code_soft_locks(monkeypatch, capsys):
    """空码不退出进程：记录 startup_error 转软锁，stderr 给运维完整提示。"""
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    lic.enforce("")
    assert lic.startup_error and "Invalid license code" in lic.startup_error and "empty" in lic.startup_error
    assert "License" in capsys.readouterr().err


async def test_enforce_compiled_bad_sig_soft_locks(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    lic.enforce(make_code(tamper=True))
    assert lic.startup_error and "signature" in lic.startup_error
    assert "signature" in capsys.readouterr().err


async def test_enforce_compiled_expired_soft_locks(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    lic.enforce(make_code({"exp": int(time.time()) - 10}))
    assert lic.startup_error and "expired" in lic.startup_error
    assert "expired" in capsys.readouterr().err


async def test_enforce_compiled_valid_passes(monkeypatch, capsys):
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    lic.enforce(make_code())
    assert lic.current is not None
    assert lic.startup_error is None
    assert lic.lock_reason() is None
    assert capsys.readouterr().err == ""


async def test_empty_baked_key_fails_secure(monkeypatch, capsys):
    """内置公钥为空（镜像构建遗漏，部署方无法自修）：仍拒绝启动。"""
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", "")
    monkeypatch.setattr(lic, "__file__", SO_FILE)
    with pytest.raises(SystemExit):
        lic.enforce(make_code())
    assert "public key" in capsys.readouterr().err


# ============================================================
# 内置公钥（唯一来源，编译进 .so）：异常一律 fail-secure
# ============================================================

async def test_baked_invalid_error_mentions_builtin(monkeypatch):
    """内置公钥非法：报错指明内置一路，fail-secure。"""
    monkeypatch.setattr(lic, "PUBLIC_KEY_B64", "not-base64!!")
    with pytest.raises(lic.LicenseKeyError, match="PUBLIC_KEY_B64"):
        lic.verify(make_code())


# ============================================================
# lock_reason（运行时软锁）与请求中间件
# ============================================================

async def test_lock_reason_flags(monkeypatch):
    """未加载且无启动失败（源码运行）恒 None；过期判定含边界、布尔排除。"""
    assert lic.lock_reason() is None
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) + 3600})
    assert lic.lock_reason() is None
    monkeypatch.setattr(lic, "current", {"exp": int(time.time())})
    assert "expired" in lic.lock_reason()
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) - 1})
    assert "expired" in lic.lock_reason()
    monkeypatch.setattr(lic, "current", {"sub": "no-exp"})
    assert lic.lock_reason() is None
    # 启动失败优先于过期判定
    monkeypatch.setattr(lic, "startup_error", "Invalid license code: License code is empty")
    assert "empty" in lic.lock_reason()


async def test_soft_lock_middleware(client, monkeypatch):
    """未授权后业务 API 即刻 503（对外只提示「无效的授权码」），/health 豁免。"""
    # 未授权（current 为 None 且无启动失败）：放行
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/admin-api/pay/channels")).status_code != 503
    # 过期：业务路径断流，健康检查照常；对外不区分原因
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) - 1})
    r = await client.get("/admin-api/pay/channels")
    assert r.status_code == 503
    assert r.json()["message"] == "Invalid License Code"
    assert (await client.get("/health")).status_code == 200


async def test_soft_lock_middleware_html_page(client, monkeypatch):
    """浏览器页面请求（Accept: text/html）返回自包含「无效的授权码」提示页，
    不泄露原因与配置方法；启动失败与过期同样只出该提示。"""
    for lock_state in (
        {"startup_error": "Invalid license code: License code is empty"},
        {"current": {"exp": int(time.time()) - 1}},
    ):
        monkeypatch.setattr(lic, "startup_error", lock_state.get("startup_error"))
        monkeypatch.setattr(lic, "current", lock_state.get("current"))
        r = await client.get("/", headers={"accept": "text/html"})
        assert r.status_code == 503
        assert "Invalid License Code" in r.text
        # 公开页面不泄露原因/配置细节
        assert "empty" not in r.text and ".env" not in r.text and "expired" not in r.text
        assert "text/html" in r.headers["content-type"]


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


# ============================================================
# 到期日期：expire_date 助手 + 公开端点 /license/expire
# ============================================================

async def test_expire_date_helper(monkeypatch):
    assert lic.expire_date() is None
    exp = int(time.time()) + 3600
    monkeypatch.setattr(lic, "current", {"exp": exp})
    assert lic.expire_date() == time.strftime("%Y-%m-%d", time.localtime(exp))


async def test_license_expire_endpoint(client, monkeypatch):
    """公开端点直接输出到期日期（YYYY-MM-DD）；未加载授权输出「未授权」。"""
    monkeypatch.setattr(lic, "current", None)
    r = await client.get("/license/expire")
    assert r.status_code == 200
    assert r.text == "Unlicensed"

    exp = int(time.time()) + 3600
    monkeypatch.setattr(lic, "current", {"exp": exp})
    r = await client.get("/license/expire")
    assert r.status_code == 200
    assert r.text == time.strftime("%Y-%m-%d", time.localtime(exp))
    assert r.headers["content-type"].startswith("text/plain")

    # 软锁（启动失败/过期）期间仍可访问，便于排障与监控
    monkeypatch.setattr(lic, "startup_error", "Invalid license code: License code is empty")
    r = await client.get("/license/expire")
    assert r.status_code == 200
    assert r.text == time.strftime("%Y-%m-%d", time.localtime(exp))
