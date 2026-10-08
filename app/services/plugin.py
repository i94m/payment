"""插件注册表 — 扫描 plugins/ 目录自动发现。

- 注册键取 info.json 的 id（缺省目录名）
- 插件元信息（凭证表单 schema 等）优先读插件类的 meta 属性（PluginMeta），
  兼容只写 info.json 的旧式插件
- 兼容旧式同步插件：submit/notify 返回值若为可等待对象则 await，
  否则原样返回（由调用方做形态适配）
"""
import asyncio
import importlib
import json
import logging
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
    _payment: dict[str, type[BasePayment]] = {}
    _sms: dict[str, type[BaseSms]] = {}
    _mail: dict[str, type[BaseMail]] = {}
    _loaded = False

    TYPE_MAP = {
        "payment": ("_payment", BasePayment),
        "sms": ("_sms", BaseSms),
        "mail": ("_mail", BaseMail),
    }

    @classmethod
    def discover(cls, base_path: str | None = None):
        if cls._loaded:
            return
        root = Path(base_path or "plugins")
        for plugin_type, (attr, base_cls) in cls.TYPE_MAP.items():
            registry = getattr(cls, attr)
            type_dir = root / plugin_type
            if not type_dir.exists():
                continue
            for d in sorted(type_dir.iterdir()):
                if not d.is_dir():
                    continue
                if d.name.startswith("_"):
                    # `_` 前缀目录是模板/示例（如 _example），不注册、不在后台展示
                    continue
                plugin_file = d / "plugin.py"
                if not plugin_file.exists():
                    continue
                info_file = d / "info.json"
                info = {}
                if info_file.exists():
                    try:
                        info = json.loads(info_file.read_text())
                    except Exception:
                        info = {}
                try:
                    mod = importlib.import_module(f"plugins.{plugin_type}.{d.name}.plugin")
                    for name in dir(mod):
                        obj = getattr(mod, name)
                        if isinstance(obj, type) and issubclass(obj, base_cls) and obj is not base_cls:
                            plugin_id = info.get("id", d.name)
                            registry[plugin_id] = obj
                            break
                except Exception as e:
                    _log.warning(f"加载插件失败 {d.name}: {e}")
        cls._loaded = True

    @classmethod
    def reload(cls):
        """强制重新扫描（admin 刷新插件列表）。"""
        cls._loaded = False
        cls._payment.clear()
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
    def _info_json(cls, plugin_name: str) -> dict:
        """读插件目录的 info.json（取不到返回空 dict）。"""
        root = Path("plugins/payment") / plugin_name / "info.json"
        if root.exists():
            try:
                return json.loads(root.read_text())
            except Exception:
                pass
        return {}

    @classmethod
    def plugin_meta(cls, plugin_name: str) -> Optional[PluginMeta]:
        """取插件元信息（优先类上的 meta，缺省从 info.json 兜底）。"""
        plugin_cls = cls.get_payment_class(plugin_name)
        if not plugin_cls:
            return None
        meta = getattr(plugin_cls, "meta", None)
        if isinstance(meta, PluginMeta):
            return meta
        # 兜底：从 info.json 构造
        info = cls._info_json(plugin_name)
        if info:
            return PluginMeta(
                name=plugin_name,
                showname=info.get("title", plugin_name),
                types=info.get("types", []),
                inputs=info.get("inputs", []),
            )
        return None

    @classmethod
    def list_plugins(cls) -> list[dict]:
        """扫描 plugins/payment 目录，返回全部插件元信息（供 admin）。"""
        cls.discover()
        result = []
        for name, plugin_cls in cls._payment.items():
            meta = cls.plugin_meta(name)
            version = str(cls._info_json(name).get("version", "") or "")
            if meta:
                result.append({
                    "name": meta.name,
                    "showname": meta.showname,
                    "types": meta.types,
                    "inputs": meta.inputs,
                    "version": version,
                    "refund": bool(getattr(plugin_cls, "is_refund_supported", lambda: False)()),
                })
            else:
                result.append({"name": name, "showname": name, "types": [], "inputs": [], "version": version, "refund": False})
        return result
