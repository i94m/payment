"""后台上传插件（.so 插件包）：解包安装 / 安全校验 / 注册表加载。

导入成功路径用 monkeypatch 假模块模拟（真实 .so 与平台 ABI 相关，无法在
测试环境通载）；解包、安全校验、目录落盘、Plugin 表同步均走真实逻辑。
"""
import io
import types
import zipfile

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import SystemAdmin
from app.services.plugin import PluginRegistry
from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult, PluginMeta

SO_NAME = "plugin.cpython-312-x86_64-linux-gnu.so"


def _make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _so_zip(name: str = "myplug") -> bytes:
    # 插件包只有 plugin.so（info.json 已彻底废除，不再参与元信息）
    return _make_zip({
        f"{name}/{SO_NAME}": b"\x7fELF-fake-plugin-so",
    })


class _FakePlugin(BasePayment):
    # 模拟真实编译插件：元信息/版本号全部来自类 meta（编译保留类属性）
    meta = PluginMeta(name="myplug", showname="测试插件", types=["alipay"], version="1.0.1")

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        return PaymentResult(kind="error", msg="stub")


class _FakePluginMismatch(BasePayment):
    # meta.name 与 zip 目录名不一致：安装必须被拒绝（防注册键错位）
    meta = PluginMeta(name="otherplug", showname="错名插件", types=["alipay"], version="1.0.0")

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        return PaymentResult(kind="error", msg="stub")


class _FakeEpayOverride(BasePayment):
    # 三方目录里的同名插件：应覆盖镜像内置的 epay，且来源标记为 third
    meta = PluginMeta(name="epay", showname="覆盖版epay", types=["alipay"], version="9.9.9")

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        return PaymentResult(kind="error", msg="stub")


class _FakeDropin(BasePayment):
    # 手工拷入 storage/plugins/dropin/ 的插件（目录名与 meta.name 一致）
    meta = PluginMeta(name="dropin", showname="拷入插件", types=["alipay"], version="0.1.0")

    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        return PaymentResult(kind="error", msg="stub")


@pytest.fixture
def upload_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    monkeypatch.setattr(PluginRegistry, "UPLOAD_ROOT", root)
    return root


@pytest.fixture
async def admin_headers(session):
    session.add(SystemAdmin(name="管理员", username="plugadmin",
                            password=hash_password("admin123"), is_super_admin=True))
    await session.commit()
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "plugadmin"))).first()
    token_obj = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token_obj.access_token}"}


async def test_upload_rejects_py_source(client, upload_root, admin_headers):
    """含 .py 源码的包一律拒绝（与镜像源码保护口径一致）。"""
    data = _make_zip({"myplug/plugin.py": b"class Plugin: pass"})
    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("p.zip", data, "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] != 0
    assert ".py" in body["message"]
    assert not upload_root.exists() or not any(upload_root.iterdir())


async def test_upload_requires_plugin_so(client, upload_root, admin_headers):
    """缺少 plugin*.so 主模块的包拒绝。"""
    data = _make_zip({"myplug/info.json": b"{}"})
    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("p.zip", data, "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] != 0
    assert "plugin.so" in body["message"]


async def test_upload_zip_slip_rejected(client, upload_root, admin_headers):
    """越界路径（zip-slip）直接拒绝，不落任何文件。"""
    data = _make_zip({f"myplug/{SO_NAME}": b"\x7fELF", "myplug/../evil.txt": b"x"})
    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("p.zip", data, "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] != 0
    assert "非法路径" in body["message"]
    assert not upload_root.exists() or not any(upload_root.iterdir())


async def test_upload_install_success(client, upload_root, admin_headers, monkeypatch):
    """安装成功：文件夹形式落盘、注册表可查、Plugin 表立即可见。"""

    def _fake_import(cls, name, so_path):
        mod = types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = _FakePlugin
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))

    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("myplug.zip", _so_zip("myplug"), "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["name"] == "myplug"
    assert body["data"]["showname"] == "测试插件"

    # 安装目录保持文件夹形式且无 .py、无 info.json
    assert (upload_root / "myplug" / SO_NAME).is_file()
    assert not (upload_root / "myplug" / "info.json").exists()
    assert not any(upload_root.rglob("*.py"))

    # 注册表可查 + Plugin 表已同步（后台「已安装插件」立即可见）
    assert "myplug" in PluginRegistry.payment_names()
    resp = await client.get("/admin-api/pay/plugins", headers=admin_headers)
    names = {p["name"]: p for p in resp.json()["data"]}
    assert names["myplug"]["showname"] == "测试插件"
    assert names["myplug"]["version"] == "1.0.1"  # 版本号来自类 meta，不再来自 info.json
    assert names["myplug"]["source"] == "third"  # 上传/三方目录来源标记
    # 镜像内置插件标记为 builtin
    assert PluginRegistry.plugin_source("epay") == "builtin"


async def test_third_party_dir_overrides_builtin(upload_root, monkeypatch):
    """三方目录（storage/plugins）同名插件覆盖镜像内置，来源标记 third。"""
    import types as _types

    def _fake_import(cls, name, so_path):
        mod = _types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = _FakeEpayOverride
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))
    # 模拟部署方直接拷入 storage/plugins/epay/plugin*.so（无 .py）
    plug_dir = upload_root / "epay"
    plug_dir.mkdir(parents=True)
    (plug_dir / SO_NAME).write_bytes(b"\x7fELF-fake-override")

    try:
        PluginRegistry.reload()
        assert PluginRegistry.get_payment_class("epay") is _FakeEpayOverride
        assert PluginRegistry.plugin_source("epay") == "third"
        metas = {m["name"]: m for m in PluginRegistry.list_plugins()}
        assert metas["epay"]["showname"] == "覆盖版epay"
        assert metas["epay"]["version"] == "9.9.9"
        assert metas["epay"]["source"] == "third"
    finally:
        # 清掉临时三方目录并重扫，避免污染后续用例
        for p in plug_dir.iterdir():
            p.unlink()
        plug_dir.rmdir()
        PluginRegistry.reload()
    assert PluginRegistry.plugin_source("epay") == "builtin"


async def test_upload_ignores_info_json(client, upload_root, admin_headers, monkeypatch):
    """包内残留 info.json 一律忽略：不落盘、不解析（彻底废除口径）。"""

    def _fake_import(cls, name, so_path):
        mod = types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = _FakePlugin
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))

    data = _make_zip({
        f"myplug/{SO_NAME}": b"\x7fELF-fake-plugin-so",
        "myplug/info.json": b'{"id": "evil", "title": "fake", "version": "9.9.9"}',
    })
    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("myplug.zip", data, "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] == 0, body
    # 插件名/版本以目录名与类 meta 为准，info.json 被忽略
    assert body["data"]["name"] == "myplug"
    assert not (upload_root / "myplug" / "info.json").exists()
    resp = await client.get("/admin-api/pay/plugins", headers=admin_headers)
    names = {p["name"]: p for p in resp.json()["data"]}
    assert names["myplug"]["version"] == "1.0.1"


async def test_upload_meta_name_mismatch_rolls_back(client, upload_root, admin_headers, monkeypatch):
    """meta.name 与 zip 目录名不一致：拒绝安装并回滚（防注册键错位）。"""

    def _fake_import(cls, name, so_path):
        mod = types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = _FakePluginMismatch
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))

    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("myplug.zip", _so_zip("myplug"), "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] != 0
    assert "插件名不一致" in body["message"]
    assert not (upload_root / "myplug").exists()
    assert "myplug" not in PluginRegistry.payment_names()


async def test_upload_bad_so_rolls_back(client, upload_root, admin_headers, monkeypatch):
    """导入失败（坏 .so）返回错误且不保留半成品目录。"""
    r = await client.post("/admin-api/pay/plugins/upload",
                          files={"file": ("myplug.zip", _so_zip("badplug"), "application/zip")},
                          headers=admin_headers)
    body = r.json()
    assert body["code"] != 0
    assert "导入失败" in body["message"]
    assert not (upload_root / "badplug").exists()
    assert "badplug" not in PluginRegistry.payment_names()


async def test_dropped_dir_visible_and_synced_without_restart(
        client, upload_root, admin_headers, monkeypatch):
    """直接往 storage/plugins 拷目录：点一次「已安装插件」就能看到并落库，无需重启。"""
    monkeypatch.setattr(PluginRegistry, "CHECK_INTERVAL", 0.0)

    def _fake_import(cls, name, so_path):
        mod = types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = _FakeDropin
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))
    # 模拟部署方 cp -r 拷入：既没上传、也没点同步、更没重启进程
    plug_dir = upload_root / "dropin"
    plug_dir.mkdir(parents=True)
    (plug_dir / SO_NAME).write_bytes(b"\x7fELF-fake-dropin")

    # 列表页这一次 GET 就自动发现盘上新目录（pending=还没同步进 plugins 表）
    resp = await client.get("/admin-api/pay/plugins", headers=admin_headers)
    names = {p["name"]: p for p in resp.json()["data"]}
    assert names["dropin"]["pending"] is True
    assert names["dropin"]["source"] == "third"
    assert names["dropin"]["showname"] == "拷入插件"
    assert names["dropin"]["supported_types"] == ["alipay"]
    assert PluginRegistry.get_payment_class("dropin") is _FakeDropin

    # 点「已安装插件（N）」= 同步：落库 + 广播（其他进程 ≤1 秒跟上）
    r = await client.post("/admin-api/pay/plugins/sync", headers=admin_headers)
    body = r.json()
    assert body["code"] == 0, body
    assert "已同步" in body["data"]["message"]
    assert body["data"]["synced"] >= 1

    resp = await client.get("/admin-api/pay/plugins", headers=admin_headers)
    names = {p["name"]: p for p in resp.json()["data"]}
    assert not names["dropin"].get("pending")
    assert names["dropin"]["version"] == "0.1.0"


def test_stamp_broadcast_reloads_other_processes(upload_root, monkeypatch):
    """reload() 写刷新戳 = 跨进程广播：其他 worker/副本无需重启即可重扫到新版本。"""
    monkeypatch.setattr(PluginRegistry, "CHECK_INTERVAL", 0.0)
    version = {"v": "1.0.0"}

    def _fake_import(cls, name, so_path):
        mod = types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = type("_HotPlug", (_FakePlugin,), {
            "meta": PluginMeta(name="hotplug", showname="热更插件",
                               types=["alipay"], version=version["v"]),
        })
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))
    plug_dir = upload_root / "hotplug"
    plug_dir.mkdir(parents=True)
    so = plug_dir / SO_NAME
    so.write_bytes(b"v1")
    PluginRegistry.reload()
    assert PluginRegistry.plugin_meta("hotplug").version == "1.0.0"

    # 原位替换 .so（父目录 mtime 不变）：本进程不该凭空知道，必须先有广播
    version["v"] = "2.0.0"
    so.write_bytes(b"v2")
    PluginRegistry.discover()
    assert PluginRegistry.plugin_meta("hotplug").version == "1.0.0"

    # 另一个进程点了刷新/上传（写戳）→ 本进程下一次检查即重扫
    PluginRegistry.mark_dirty()
    PluginRegistry.discover()
    assert PluginRegistry.plugin_meta("hotplug").version == "2.0.0"

    # 重扫不写戳：多进程不会互相触发无限重扫
    assert PluginRegistry._sig == PluginRegistry._fingerprint()
    PluginRegistry.discover()
    assert PluginRegistry.plugin_meta("hotplug").version == "2.0.0"


def test_page_refresh_forces_fingerprint_check(upload_root, monkeypatch):
    """后台刷新页面走 discover(force=True)：不受业务侧节流限制，一定拿到盘上最新。"""
    monkeypatch.setattr(PluginRegistry, "CHECK_INTERVAL", 3600.0)
    version = {"v": "1.0.0"}

    def _fake_import(cls, name, so_path):
        mod = types.ModuleType(f"plugins_upload.{name}.plugin")
        mod.Plugin = type("_PagePlug", (_FakePlugin,), {
            "meta": PluginMeta(name="pageplug", showname="页面插件",
                               types=["alipay"], version=version["v"]),
        })
        return mod

    monkeypatch.setattr(PluginRegistry, "_import_upload", classmethod(_fake_import))
    plug_dir = upload_root / "pageplug"
    plug_dir.mkdir(parents=True)
    so = plug_dir / SO_NAME
    so.write_bytes(b"v1")
    PluginRegistry.reload()
    assert PluginRegistry.plugin_meta("pageplug").version == "1.0.0"

    # 另一个 worker 装了新版本（写戳），本进程还在节流窗口内
    version["v"] = "2.0.0"
    so.write_bytes(b"v2")
    PluginRegistry.mark_dirty()
    PluginRegistry.discover()  # 业务热点路径：节流窗口内不重扫（可接受）
    assert PluginRegistry.plugin_meta("pageplug").version == "1.0.0"

    # 后台刷新列表：强制比对，立刻就是最新
    PluginRegistry.discover(force=True)
    assert PluginRegistry.plugin_meta("pageplug").version == "2.0.0"

    # 无变化时 force 只比对不重扫（不会每次刷页面都重新导入 .so）
    cls_after = PluginRegistry.get_payment_class("pageplug")
    PluginRegistry.discover(force=True)
    assert PluginRegistry.get_payment_class("pageplug") is cls_after
