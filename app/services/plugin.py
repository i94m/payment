"""插件注册表 — 支付插件扫描 plugins/ 自动发现，短信/邮件适配器在 app/addons/。

- 注册键 = 插件目录名（`_` 前缀目录跳过）
- 插件元信息（凭证表单 schema / 版本号等）全部来自插件类的 meta 属性（PluginMeta），
  不再读取 info.json
- 支付插件分两档：builtin=镜像内置（`plugins/`，随镜像编译），
  third=三方（`storage/plugins/`，后台上传或部署方直接拷入 .so 目录），
  同名时三方覆盖内置，无需重建镜像即可上线新插件
- 三方插件的包签名只在「后台上传安装」环节校验（`app/core/plugin_signing.py`，
  编译镜像强制、未签名/被改动直接拒绝）；部署方手工拷进 `storage/plugins/` 的插件
  默认信任、加载时不再验签（能操作服务器文件即最高权限）

- 跨进程刷新：多 worker/多副本部署下注册表是进程内内存态，单进程 reload()
  只影响自己。因此 reload() 会写一个「刷新戳」文件（三方目录内），
  其他进程在热点路径上按指纹（戳内容 + 目录 mtime）发现变化后自动重扫，
  所以后台上传/点「刷新插件」后，业务侧进程按 CHECK_INTERVAL 跟上，无需重启
- 兼容旧式同步插件：submit/notify 返回值若为可等待对象则 await，
  否则原样返回（由调用方做形态适配）
"""
import asyncio
import importlib
import importlib.machinery
import importlib.util
import logging
import sys
import time
import uuid
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
    # 跨进程刷新戳（写在三方目录内，随 storage 持久化；扫描时按非目录跳过）
    STAMP_NAME = ".reload.stamp"
    # 业务热点路径（每次取插件）检查磁盘指纹的最小间隔（秒）。
    # 后台页面刷新不看这个间隔（discover(force=True) 强制比对），所以调大它只影响
    # 「其他 worker 何时跟上新代码」，不影响后台看到的状态是否为最新。
    CHECK_INTERVAL = 5.0
    _payment: dict[str, type[BasePayment]] = {}
    # 插件来源：builtin=镜像内置（plugins/），third=三方（storage/plugins，覆盖内置）
    _payment_source: dict[str, str] = {}
    _sms: dict[str, type[BaseSms]] = {}
    _mail: dict[str, type[BaseMail]] = {}
    _loaded = False
    # 上次扫描时的「插件目录指纹」与检查时间（跨进程刷新用）
    _sig: Optional[str] = None
    _checked_at: float = 0.0
    # 最近一次扫描中加载失败的插件：目录名 → 原因（排障用：上传报错/后台列表展示）
    _load_errors: dict[str, str] = {}

    # (注册表属性, 基类, 包名)：扫描目录 = 包名转路径，import 模块串 = 包名派生
    TYPE_MAP = {
        "payment": ("_payment", BasePayment, "plugins"),
        "sms": ("_sms", BaseSms, "app.addons.sms"),
        "mail": ("_mail", BaseMail, "app.addons.mail"),
    }

    @classmethod
    def discover(cls, force: bool = False):
        """按需扫描；磁盘指纹变化时自动重扫（跨 worker/副本一致）。

        指纹 = 刷新戳内容 + 三方插件目录 mtime，因此两种场景都能自动发现：
        - 后台「同步插件」/上传安装 → reload() 写戳 → 其他进程随后跟上
        - 部署方直接拷入/删除插件目录 → 目录 mtime 变化 → 同样自动发现

        force=True 忽略节流窗口、立刻比对一次（后台插件列表页用）：
        保证「换个 worker 刷新页面也一定是盘上最新状态」。未变化时不重扫。
        """
        if not cls._loaded:
            cls._rescan()
            return
        if not force:
            if not cls._fingerprint_changed():
                return
            cls._rescan()
            return
        cls._checked_at = time.monotonic()
        if cls._fingerprint() != cls._sig:
            cls._rescan()

    @classmethod
    def _fingerprint_changed(cls) -> bool:
        """节流后的指纹比对：业务热点路径每 CHECK_INTERVAL 秒最多一次 stat。"""
        now = time.monotonic()
        if now - cls._checked_at < cls.CHECK_INTERVAL:
            return False
        cls._checked_at = now
        return cls._fingerprint() != cls._sig

    @classmethod
    def _fingerprint_changed(cls) -> bool:
        """节流后的指纹比对：热点路径每秒最多一次 stat。"""
        now = time.monotonic()
        if now - cls._checked_at < cls.CHECK_INTERVAL:
            return False
        cls._checked_at = now
        return cls._fingerprint() != cls._sig

    @classmethod
    def _stamp_path(cls) -> Path:
        return cls.UPLOAD_ROOT / cls.STAMP_NAME

    @classmethod
    def _fingerprint(cls) -> str:
        """插件目录指纹：戳文件内容 + 三方目录 mtime。

        戳内容（uuid）覆盖「同目录内替换 .so」这种不改父目录 mtime 的更新；
        目录 mtime 覆盖「直接拷入/删除插件目录」这种没走后台的场景。
        """
        try:
            with open(cls._stamp_path(), "rb") as fh:
                stamp = fh.read(64).decode("utf-8", "ignore")
        except OSError:
            stamp = ""
        try:
            dir_mtime = str(cls.UPLOAD_ROOT.stat().st_mtime_ns)
        except OSError:
            dir_mtime = ""
        return f"{stamp}:{dir_mtime}"

    @classmethod
    def _rescan(cls):
        """本进程重扫（不写戳）：广播由 reload()/mark_dirty() 负责。

        本方法绝不写戳 —— 否则多进程会互相触发无限重扫。
        """
        # 上传/三方插件按文件导入会写 sys.modules，重扫前清掉才能加载到新代码
        # （含虚拟包根 plugins_upload：其 __path__ 绑定的是上一次的目录）
        for k in [k for k in sys.modules
                  if k == "plugins_upload" or k.startswith("plugins_upload.")]:
            del sys.modules[k]
        cls._payment.clear()
        cls._payment_source.clear()
        cls._sms.clear()
        cls._mail.clear()
        cls._load_errors.clear()
        cls._checked_at = time.monotonic()
        for plugin_type, (attr, base_cls, pkg) in cls.TYPE_MAP.items():
            registry = getattr(cls, attr)
            dirs = [(Path(pkg.replace(".", "/")), pkg, True, "builtin")]
            if plugin_type == "payment":
                # 额外扫描三方插件目录（不在包路径上，按文件位置导入）；
                # 后扫描覆盖先扫描 → 同名三方插件优先于镜像内置
                dirs.append((cls.UPLOAD_ROOT, "", False, "third"))
            for type_dir, pkg_name, from_pkg, source in dirs:
                cls._scan_type_dir(type_dir, base_cls, pkg_name, from_pkg, registry, source)
        # 指纹取扫描结束时的状态：并发写戳/拷目录会留到下一次检查再补扫
        cls._sig = cls._fingerprint()
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
                    _log.warning(f"加载插件失败 {d.name}: {e}", exc_info=True)
                    cls._remember_error(d.name, e)
                    continue
            else:
                # 三方目录只允许编译后的 .so（安全限制：不接受 .py 源码）
                so_path = cls._find_plugin_module(d)
                if so_path is None:
                    continue
                # 说明：签名校验只在「后台上传安装」那一步（见 admin/channel.py 的
                # plugin_upload）。部署方手工拷进 storage/plugins/ 的插件默认信任 ——
                # 能操作服务器文件本身就是最高权限，不在加载时再拦。
                try:
                    mod = cls._import_upload(d.name, so_path)
                except Exception as e:
                    _log.warning(f"加载插件失败 {d.name}: {e}", exc_info=True)
                    cls._remember_error(d.name, e)
                    continue
            for name in dir(mod):
                obj = getattr(mod, name)
                if isinstance(obj, type) and issubclass(obj, base_cls) and obj is not base_cls:
                    # 注册键 = 目录名；meta.name 与目录名的一致性由上传校验，
                    # 源码目录内不一致的按目录名注册（后台 meta 展示以类属性为准）
                    plugin_id = d.name
                    registry[plugin_id] = obj
                    cls._load_errors.pop(plugin_id, None)
                    if registry is cls._payment:
                        cls._payment_source[plugin_id] = source
                        meta = getattr(obj, "meta", None)
                        if isinstance(meta, PluginMeta) and meta.name != plugin_id:
                            # 手拷目录不做上传校验，这里只告警：注册键仍是目录名，
                            # 通道 plugin_name 必须用目录名（后台列表按目录名展示/选择）
                            _log.warning(
                                f"插件目录名与 meta.name 不一致：目录 {plugin_id} / "
                                f"meta.name {meta.name}（按目录名注册）")
                    break
            else:
                # 模块导入成功但没有 BasePayment/Sms/Mail 子类：多半是编译时基类不对
                if d.name not in registry and d.name not in cls._load_errors:
                    cls._remember_error(d.name, None)

    @classmethod
    def _remember_error(cls, plugin_id: str, exc: Optional[BaseException]) -> None:
        """记录加载失败原因（给后台展示 / 上传接口回显，便于排障）。"""
        if exc is None:
            cls._load_errors[plugin_id] = (
                "模块已加载但没有 BasePayment 子类：请确认插件类继承自 "
                "app.services.plugin_interfaces.BasePayment 且编译进了同一个模块")
        else:
            cls._load_errors[plugin_id] = f"{type(exc).__name__}: {exc}"

    @classmethod
    def plugin_load_error(cls, plugin_name: str) -> Optional[str]:
        """某插件最近一次加载失败的原因（None=无错误/已成功加载）。"""
        return cls._load_errors.get(plugin_name)

    @classmethod
    def load_errors(cls) -> dict[str, str]:
        """最近一次扫描里所有加载失败的插件（目录名 → 原因）。"""
        return dict(cls._load_errors)

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
        """强制重扫并广播：后台同步/上传安装/启动后调用。

        本进程立即生效；其他进程靠刷新戳在 ≤CHECK_INTERVAL 秒内自动重扫，
        多 worker/多副本部署不再需要重启容器。
        """
        cls.mark_dirty()
        cls._rescan()

    @classmethod
    def mark_dirty(cls):
        """写跨进程刷新戳（其他进程发现戳变化后自动重扫）。

        写失败只影响跨进程刷新（本进程照常生效），记 warning 不抛异常。
        """
        try:
            cls.UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
            cls._stamp_path().write_text(uuid.uuid4().hex, encoding="utf-8")
        except OSError as e:
            _log.warning(f"插件刷新戳写入失败（其他进程需重启才能生效）: {e}")

    @classmethod
    def _lookup(cls, registry: dict, name: str) -> Any:
        """查注册表；未命中时重扫一次本进程。

        重扫不广播：未命中可能只是本进程没见过（或该插件确实不存在），
        若每次调用都广播，坏通道会把所有进程反复拖去重扫。
        """
        cls.discover()
        obj = registry.get(name)
        if obj is None:
            cls._rescan()
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
                    # 注册键 = 目录名，与通道 plugin_name 同一口径（手拷目录名
                    # 与 meta.name 不一致时仍可正确建通道；展示名用 showname）
                    "name": name,
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
