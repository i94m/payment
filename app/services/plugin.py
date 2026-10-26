"""插件注册表 — 支付插件扫描 plugins/ 自动发现，短信/邮件适配器在 app/addons/。

- 注册键 = 插件目录名（`_` 前缀目录跳过）
- 插件元信息（凭证表单 schema / 版本号等）全部来自插件类的 meta 属性（PluginMeta），
  不再读取 info.json
- 支付插件分两档：builtin=镜像内置（`plugins/`，随镜像编译），
  third=三方（`storage/plugins/`，后台上传或部署方直接拷入 .so 目录），
  同名时三方覆盖内置，无需重建镜像即可上线新插件
- 兼容旧式同步插件：submit/notify 返回值若为可等待对象则 await，
  否则原样返回（由调用方做形态适配）
"""
import asyncio
import importlib
import importlib.machinery
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from app.services.plugin_interfaces import (
    BasePayment,
    BaseSms,
    BaseMail,
    PluginMeta,
)

_log = logging.getLogger(__name__)


async def maybe_await(result: Any) -> Any:
    """兼容同步插件：结果若为 coroutine 则等待。"""
    if asyncio.iscoroutine(result):
        return await result
    return result


class PluginRegistry:
    # 后台上传插件的安装目录（storage 持久卷，容器重建不丢；payment 专属）。
    # 同时也是「三方插件」目录：部署方可直接把编译好的 plugin*.so 目录拷进来，
    # 无需重建镜像；与后台上传同一目录、优先级高于镜像内置插件。
    UPLOAD_ROOT = Path("storage") / "plugins"
    _payment: dict[str, type[BasePayment]] = {}
    # 插件来源：builtin=镜像内置（plugins/），third=三方（storage/plugins，覆盖内置）
    _payment_source: dict[str, str] = {}
    _sms: dict[str, type[BaseSms]] = {}
    _mail: dict[str, type[BaseMail]] = {}
    _loaded = False

    # (注册表属性, 基类, 包名)：扫描目录 = 包名转路径，import 模块串 = 包名派生
    TYPE_MAP = {
        "payment": ("_payment", BasePayment, "plugins"),
        "sms": ("_sms", BaseSms, "app.addons.sms"),
        "mail": ("_mail", BaseMail, "app.addons.mail"),
    }

    @classmethod
    def discover(cls):
        if cls._loaded:
            return
        for plugin_type, (attr, base_cls, pkg) in cls.TYPE_MAP.items():
            registry = getattr(cls, attr)
            dirs = [(Path(pkg.replace(".", "/")), pkg, True, "builtin")]
            if plugin_type == "payment":
                # 额外扫描三方插件目录（不在包路径上，按文件位置导入）；
                # 后扫描覆盖先扫描 → 同名三方插件优先于镜像内置
                dirs.append((cls.UPLOAD_ROOT, "", False, "third"))
            for type_dir, pkg_name, from_pkg, source in dirs:
                cls._scan_type_dir(type_dir, base_cls, pkg_name, from_pkg, registry, source)
        cls._loaded = True

    @classmethod
    def _scan_type_dir(cls, type_dir: Path, base_cls, pkg: str, from_pkg: bool, registry: dict,
                       source: str = "builtin"):
        if not type_dir.exists():
            return
        for d in sorted(type_dir.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith("_"):
                # `_` 前缀目录是模板/示例（如 _example），不注册、不在后台展示
                continue
            if from_pkg:
                # 编译镜像里插件已是 .so，不再存在 plugin.py；
                # 按 import 系统后缀表探测模块是否存在（.py 与 .so 通用）
                try:
                    if importlib.util.find_spec(f"{pkg}.{d.name}.plugin") is None:
                        continue
                except (ImportError, ValueError):
                    continue
                try:
                    mod = importlib.import_module(f"{pkg}.{d.name}.plugin")
                except Exception as e:
                    _log.warning(f"加载插件失败 {d.name}: {e}")
                    continue
            else:
                # 三方目录只允许编译后的 .so（安全限制：不接受 .py 源码）
                so_path = cls._find_plugin_module(d)
                if so_path is None:
                    continue
                try:
                    mod = cls._import_upload(d.name, so_path)
                except Exception as e:
                    _log.warning(f"加载插件失败 {d.name}: {e}")
                    continue
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and issubclass(obj, base_cls) and obj is not base_cls:
                    # 注册键 = 目录名；meta.name 与目录名的一致性由上传校验，
                    # 源码目录内不一致的按目录名注册（后台 meta 展示以类属性为准）
                    plugin_id = d.name
                    registry[plugin_id] = obj
                    if registry is cls._payment:
                        cls._payment_source[plugin_id] = source
                    break

    @classmethod
    def _find_plugin_module(cls, d: Path) -> Optional[Path]:
        """找插件主模块 plugin*.so（上传目录不含 .py）。

        先按本机 import 后缀表精确匹配（生产容器内的规范产物名）；
        未命中再通配 plugin*.so 兜底（包可能是其他平台/ABI 编译的，
        真正能否加载由导入时的异常兜底）。
        """
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            p = d / f"plugin{suffix}"
            if p.is_file():
                return p
        for p in sorted(d.glob("plugin*.so")):
            if p.is_file():
                return p
        return None

    @classmethod
    def _import_upload(cls, name: str, so_path: Path):
        """以正常包语义导入上传的插件 .so。

        上传目录不在包路径上，构造虚拟包 plugins_upload / plugins_upload.<name>
        （__path__ 指向真实目录），使相对/绝对包内导入与镜像内插件行为一致；
        sys.modules 由 reload() 清理，保证重扫能加载到更新后的 .so。
        """
        cls._ensure_virtual_pkg("plugins_upload", str(cls.UPLOAD_ROOT))
        parent = f"plugins_upload.{name}"
        cls._ensure_virtual_pkg(parent, str(so_path.parent))
        spec = importlib.util.spec_from_file_location(f"{parent}.plugin", so_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{parent}.plugin"] = mod
        spec.loader.exec_module(mod)
        return mod

    @staticmethod
    def _ensure_virtual_pkg(fullname: str, path: str) -> None:
        if fullname in sys.modules:
            return
        pkg = importlib.util.module_from_spec(
            importlib.util.spec_from_loader(fullname, loader=None, is_package=True))
        pkg.__path__ = [path]
        sys.modules[fullname] = pkg

    @classmethod
    def reload(cls):
        """强制重新扫描（admin 刷新插件列表 / 上传安装后）。"""
        # 上传插件按文件导入会写 sys.modules，重扫前清掉才能加载到新代码
        for k in [k for k in sys.modules if k.startswith("plugins_upload.")]:
            del sys.modules[k]
        cls._loaded = False
        cls._payment.clear()
        cls._payment_source.clear()
        cls._sms.clear()
        cls._mail.clear()
        cls.discover()

    @classmethod
    def _lookup(cls, registry: dict, name: str) -> Any:
        """查注册表；未命中时强制重扫一次。

        多 worker 部署下「同步插件」只刷新处理该请求的进程，
        其他进程要等到未命中重扫才能看到新增插件目录，
        否则新插件创建通道会报「插件不存在或未加载」。
        """
        cls.discover()
        obj = registry.get(name)
        if obj is None:
            cls.reload()
            obj = registry.get(name)
        return obj

    @classmethod
    def payment_names(cls) -> list[str]:
        """已注册支付插件名列表（启动日志 / admin 展示用）。"""
        cls.discover()
        return sorted(cls._payment)

    @classmethod
    def get_payment(cls, plugin_name: str, channel: dict) -> Optional[BasePayment]:
        plugin_cls = cls._lookup(cls._payment, plugin_name)
        if not plugin_cls:
            return None
        return plugin_cls(channel)

    @classmethod
    def get_payment_class(cls, plugin_name: str) -> Optional[type[BasePayment]]:
        return cls._lookup(cls._payment, plugin_name)

    @classmethod
    def get_sms(cls, name: str, config: dict) -> Optional[BaseSms]:
        cls_obj = cls._lookup(cls._sms, name)
        return cls_obj(config) if cls_obj else None

    @classmethod
    def get_mail(cls, name: str, config: dict) -> Optional[BaseMail]:
        cls_obj = cls._lookup(cls._mail, name)
        return cls_obj(config) if cls_obj else None

    @classmethod
    def plugin_meta(cls, plugin_name: str) -> Optional[PluginMeta]:
        """取插件元信息（类属性 meta 为唯一来源）。"""
        plugin_cls = cls.get_payment_class(plugin_name)
        if not plugin_cls:
            return None
        meta = getattr(plugin_cls, "meta", None)
        if isinstance(meta, PluginMeta):
            return meta
        return None

    @classmethod
    def plugin_source(cls, plugin_name: str) -> str:
        """插件来源：builtin=镜像内置（plugins/），third=三方（storage/plugins）。

        注册表未命中时返回空串（调用方自行兜底）。
        """
        cls.discover()
        return cls._payment_source.get(plugin_name, "")

    @classmethod
    def list_plugins(cls) -> list[dict]:
        """扫描 plugins/ 目录，返回全部支付插件元信息（供 admin）。"""
        cls.discover()
        result = []
        for name, plugin_cls in cls._payment.items():
            meta = cls.plugin_meta(name)
            if meta:
                result.append({
                    "name": meta.name,
                    "showname": meta.showname,
                    "types": meta.types,
                    "inputs": meta.inputs,
                    "version": meta.version,
                    "source": cls._payment_source.get(name, "builtin"),
                    "refund": bool(getattr(plugin_cls, "is_refund_supported", lambda: False)()),
                })
            else:
                result.append({"name": name, "showname": name, "types": [], "inputs": [],
                               "version": "", "source": cls._payment_source.get(name, "builtin"),
                               "refund": False})
        return result
