"""插件包签名（Ed25519）：签名/验签、摘要与插件名校验、强制开关。

校验只发生在「后台上传安装」这一步（编译镜像强制）；手工拷进 storage/plugins/ 的
插件不校验。这里用测试自带的密钥对覆盖校验逻辑（线上用内置 PLUGIN_PUBLIC_KEY_B64），
不依赖作者私钥。
"""
import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core import plugin_signing as ps
from app.services.plugin_interfaces import PluginMeta


@pytest.fixture()
def keypair():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return priv, pub_b64


def _write_package(tmp_path, name="demo", payload=b"fake-so-bytes", doc=None):
    """构造一个最小插件目录：<name>/plugin.so + signature.json（可选）。"""
    d = tmp_path / name
    d.mkdir()
    so = d / "plugin.cpython-312-x86_64-linux-gnu.so"
    so.write_bytes(payload)
    if doc is not None:
        (d / ps.SIGN_FILE).write_text(json.dumps(doc), encoding="utf-8")
    return d, so


def test_sign_and_verify_roundtrip(tmp_path, keypair):
    priv, pub = keypair
    d, so = _write_package(tmp_path)
    sha = hashlib.sha256(so.read_bytes()).hexdigest()

    doc = ps.sign_payload(ps.build_payload("demo", sha, version="1.0.0"), priv)
    payload = ps.verify_package("demo", so, doc, public_key_b64=pub)

    assert payload["name"] == "demo"
    assert payload["sha256"] == sha
    assert payload["version"] == "1.0.0"
    assert isinstance(payload["iat"], int)


def test_reject_tampered_so(tmp_path, keypair):
    priv, pub = keypair
    d, so = _write_package(tmp_path)
    sha = hashlib.sha256(so.read_bytes()).hexdigest()
    doc = ps.sign_payload(ps.build_payload("demo", sha), priv)

    # 签名落盘后再改 .so：摘要对不上
    so.write_bytes(b"tampered")
    with pytest.raises(ps.PackageSignatureError, match="摘要"):
        ps.verify_package("demo", so, doc, public_key_b64=pub)


def test_reject_wrong_name(tmp_path, keypair):
    priv, pub = keypair
    d, so = _write_package(tmp_path)
    sha = hashlib.sha256(so.read_bytes()).hexdigest()
    doc = ps.sign_payload(ps.build_payload("other-plugin", sha), priv)

    with pytest.raises(ps.PackageSignatureError, match="插件名"):
        ps.verify_package("demo", so, doc, public_key_b64=pub)


def test_reject_forged_signature(tmp_path, keypair):
    _priv, pub = keypair
    d, so = _write_package(tmp_path)
    sha = hashlib.sha256(so.read_bytes()).hexdigest()

    # 用另一把私钥签名：内置公钥验不过
    attacker = Ed25519PrivateKey.generate()
    doc = ps.sign_payload(ps.build_payload("demo", sha), attacker)
    with pytest.raises(ps.PackageSignatureError, match="签名无效"):
        ps.verify_package("demo", so, doc, public_key_b64=pub)


def test_reject_domain_prefix_swap(tmp_path, keypair):
    """域前缀必须一致：拿别的用途（如授权码）的签名冒充插件签名要失败。"""
    priv, pub = keypair
    d, so = _write_package(tmp_path)
    seg = ps._payload_seg(ps.build_payload("demo", hashlib.sha256(so.read_bytes()).hexdigest()))
    foreign_sig = priv.sign(seg.encode("ascii"))  # 少了 "PLUGIN1:" 前缀

    with pytest.raises(ps.PackageSignatureError):
        ps.verify_package("demo", so, {"payload": seg, "sig": ps._b64url_encode(foreign_sig)},
                          public_key_b64=pub)


def test_verify_plugin_dir_requires_signature_file(tmp_path, keypair):
    _priv, pub = keypair
    d, so = _write_package(tmp_path)  # 不写 signature.json
    with pytest.raises(ps.PackageSignatureError, match="signature.json"):
        ps.verify_plugin_dir(d, so, expect_name="demo", public_key_b64=pub)


def test_verify_plugin_dir_ok(tmp_path, keypair):
    priv, pub = keypair
    d, so = _write_package(tmp_path)
    sha = hashlib.sha256(so.read_bytes()).hexdigest()
    doc = ps.sign_payload(ps.build_payload("demo", sha, version="1.0.0"), priv)
    (d / ps.SIGN_FILE).write_text(json.dumps(doc), encoding="utf-8")

    payload = ps.verify_plugin_dir(d, so, public_key_b64=pub)
    assert payload["name"] == "demo"


def test_enforced_flag_source_run_default_off(monkeypatch):
    # 源码运行（pytest）：默认不强制，便于本地开发；环境变量可打开（线上 .so 恒为 True）
    monkeypatch.delenv("PLUGIN_SIGN_REQUIRED", raising=False)
    assert ps.is_enforced() is False

    monkeypatch.setenv("PLUGIN_SIGN_REQUIRED", "1")
    assert ps.is_enforced() is True


def test_loader_registers_unsigned_hand_copied_plugin(tmp_path, monkeypatch):
    """手工拷进 storage/plugins 的插件不验签：没有 signature.json 也照常注册。

    签名校验只在上传安装时执行（admin/channel.py::plugin_upload）；服务器文件权限
    本身就是最高权限，加载阶段默认信任 —— 即使把强制开关打开也不拦。
    """
    import types

    from app.services.plugin import PluginRegistry
    from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult

    d = tmp_path / "handplug"
    d.mkdir()
    so = d / "plugin.cpython-312-x86_64-linux-gnu.so"
    so.write_bytes(b"not-a-real-so")      # 导入被 monkeypatch，不会真的 dlopen
    assert not (d / "signature.json").exists()

    monkeypatch.setenv("PLUGIN_SIGN_REQUIRED", "1")

    class _Hand(BasePayment):
        meta = PluginMeta(name="handplug", showname="手拷插件", types=["alipay"], version="1.0.0")

        async def submit(self, ctx: PaymentContext) -> PaymentResult:
            return PaymentResult(kind="error", msg="stub")

    mod = types.ModuleType("plugins_upload.handplug.plugin")
    mod.Hand = _Hand

    monkeypatch.setattr(PluginRegistry, "_find_plugin_module",
                        classmethod(lambda cls, p: so))
    monkeypatch.setattr(PluginRegistry, "_import_upload",
                        classmethod(lambda cls, name, path: mod))

    registry: dict = {}
    PluginRegistry._scan_type_dir(tmp_path, BasePayment, "", False, registry, source="third")
    assert "handplug" in registry
