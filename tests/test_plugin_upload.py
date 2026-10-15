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
    return _make_zip({
        f"{name}/{SO_NAME}": b"\x7fELF-fake-plugin-so",
        f"{name}/info.json": (
            '{"id": "%s", "title": "测试插件", "types": ["alipay"], "version": "1.0.1"}' % name
        ).encode(),
    })


class _FakePlugin(BasePayment):
    # 模拟真实编译插件：类 meta 与 info.json 保持一致（编译保留类属性）
    meta = PluginMeta(name="myplug", showname="测试插件", types=["alipay"])

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

    # 安装目录保持文件夹形式且无 .py
    assert (upload_root / "myplug" / SO_NAME).is_file()
    assert (upload_root / "myplug" / "info.json").is_file()
    assert not any(upload_root.rglob("*.py"))

    # 注册表可查 + Plugin 表已同步（后台「已安装插件」立即可见）
    assert "myplug" in PluginRegistry.payment_names()
    resp = await client.get("/admin-api/pay/plugins", headers=admin_headers)
    names = {p["name"]: p for p in resp.json()["data"]}
    assert names["myplug"]["showname"] == "测试插件"
    assert names["myplug"]["version"] == "1.0.1"


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
