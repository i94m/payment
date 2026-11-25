"""admin 支付对接：通道 / 插件 / 支付方式。

- 通道凭证按插件 inputs_schema 动态渲染；落库前 Fernet 加密；查询返回脱敏
- 插件列表来自 PluginRegistry 扫描（供通道表单/筛选），支持一键同步
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
import io
import re
import shutil
import uuid
import zipfile
from typing import Optional

from fastapi import Depends, APIRouter, Query, Request, UploadFile
from pydantic import BaseModel
from sqlmodel import col, func, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import AdminUser, AnyPermissionChecker, PermissionChecker, check_permission
from app.models import Channel, ChannelCode, ChannelCodeItem, Order, PaymentType, Plugin
from app.responses import err, paginate, suc
from app.services.balance import dmoney
from app.services.channel import (
    code_amount_text,
    code_format_ok,
    get_channel_code,
    normalize_channel_code,
)
from app.services.crypto import decrypt_config, encrypt_config
from app.services.plugin import PluginRegistry
from app.services.plugin_interfaces import PluginMeta
from app.services.order import ORDER_PENDING, ORDER_TYPE_TEST
from app.services.plugin_sync import sync_plugins
import logging

from app.core import plugin_signing

_log = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# 插件
# ============================================================

def _normalize_inputs(inputs: list[dict] | None) -> list[dict]:
    """插件 inputs → 前端渲染口径（统一用 label 作展示名）。

    插件元信息里字段展示名历史上叫 `title`（与老系统 PHP 插件一致），
    前端读的是 `label`；不归一化会回退成英文 key（appurl/appid…）。
    """
    out: list[dict] = []
    for raw in inputs or []:
        item = dict(raw)
        if not item.get("label") and item.get("title"):
            item["label"] = item["title"]
        item.pop("title", None)
        out.append(item)
    return out


@router.get("/plugins", dependencies=[Depends(PermissionChecker("channel.view"))])
async def plugin_list(session: SessionDep):
    # 页面刷新即以盘上为准：强制比对一次指纹（忽略业务侧的节流窗口），
    # 保证换个 worker 刷新页面看到的也是最新插件，而不是某个进程的旧内存态
    PluginRegistry.discover(force=True)
    # Plugin 主键为 name（无自增 id），按名称排序
    rows = (await session.exec(select(Plugin).order_by(col(Plugin.name)))).all()
    data = []
    for p in rows:
        # inputs / supported_types 优先用进程内 meta（随代码即时生效），
        # 库内快照只作兜底：改插件表单字段后无需先点「刷新插件」才能看到。
        meta = PluginRegistry.plugin_meta(p.name)
        data.append({
            "name": p.name,
            "showname": meta.showname if meta else p.showname,
            "supported_types": list(meta.types) if meta else
                ((p.supported_types or "").split(",") if p.supported_types else []),
            "inputs": _normalize_inputs(meta.inputs if meta else p.inputs_schema),
            "is_enabled": bool(p.is_enabled),
            "version": (str(meta.version) if meta and meta.version else p.version) or "",
            # 插件来源：builtin=镜像内置，third=三方（storage/plugins）；行已失效时兜底 unknown
            "source": PluginRegistry.plugin_source(p.name) or "unknown",
        })

    # 盘上已装好、库里还没有的插件（手工拷目录后没点同步，或别的副本刚上传）
    # 也一并返回：后台「刷新插件」点一次即可看到并落库，无需重启
    known = {p.name for p in rows}
    for item in PluginRegistry.list_plugins():
        if item["name"] in known:
            continue
        data.append({
            "name": item["name"],
            "showname": item["showname"],
            "supported_types": item["types"],
            "inputs": _normalize_inputs(item["inputs"]),
            "is_enabled": True,
            "version": item["version"] or "",
            "source": item["source"] or "third",
            # 尚未同步进 plugins 表（点「刷新插件」即落库）
            "pending": True,
        })
    data.sort(key=lambda x: x["name"])
    return suc(data)


@router.post("/plugins/sync", dependencies=[Depends(PermissionChecker("channel.update"))])
async def plugin_sync(session: SessionDep):
    """扫描插件目录刷新注册表；盘上已不存在的插件行一并清理
    （仍被通道引用的跳过，避免通道静默失效，提示人工处理）。

    与 `db init` / `migrate upgrade` 走同一个服务函数（app/services/plugin_sync.py）。
    重扫会写跨进程刷新戳：其他 worker/副本随后自动跟上（页面刷新则强制比对、一定是最新）。
    """
    result = await sync_plugins(session)
    return suc(result)


# 上传插件包大小上限
_UPLOAD_MAX_SIZE = 10 * 1024 * 1024


@router.post("/plugins/upload", dependencies=[Depends(PermissionChecker("channel.update"))])
async def plugin_upload(file: UploadFile, session: SessionDep, request: Request,
                        auth: AdminUser):
    """上传安装编译后的插件包（zip）。需要「支付通道-更新」权限。

    装插件 = 进程内执行 .so，所以编译镜像对包做 Ed25519 验签（signature.json），
    没有作者私钥签名的包一律拒绝；权限侧与「刷新插件」一致（channel.update）。

    包结构 = scripts/build_plugin.sh 产物目录直接压缩（通常只有主模块一个文件）：
      <插件名>/plugin.cpython-312-x86_64-linux-gnu.so
    仅接受 .so 编译产物（含 .py 一律拒绝，与镜像源码保护口径一致）；插件名取
    zip 内目录名，元信息/版本号来自插件类的 meta（不再支持 info.json，包内残留的
    info.json 会被忽略）。**编译镜像还要求包内带 signature.json（Ed25519 签名）**，
    由 scripts/build_plugin.sh 打���时自动生成；未签名的包一律拒绝。安装到 storage/plugins/<插件名>/（持久卷随 storage 备份，
    容器重建不丢），安装后立即刷新注册表并同步 Plugin 表。
    重扫会写跨进程刷新戳：其他 worker/副本随后自动跟上，无需重启。
    """
    data = await file.read()
    if len(data) > _UPLOAD_MAX_SIZE:
        return err(-1, "插件包不能超过 10MB")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return err(-1, "无效的 zip 文件")

    entries = [i for i in zf.infolist() if not i.is_dir()]
    if not entries:
        return err(-1, "压缩包为空")
    if any(i.filename.endswith(".py") for i in entries):
        return err(-1, "安全限制：仅允许编译后的 .so 插件包，不接受 .py 源码")

    # 定位主模块 plugin*.so（取层级最浅的），其所在目录即插件内容。
    # 包是 linux 平台编译产物，本机 EXTENSION_SUFFIXES 不一定含其 ABI 后缀
    #（如 cpython-312-x86_64-linux-gnu），按文件名 plugin*.so 匹配即可，
    # 真正的平台兼容性由下方导入失败回滚兜底
    main_entries = [i for i in entries
                    if i.filename.rsplit("/", 1)[-1].startswith("plugin")
                    and i.filename.endswith(".so")]
    if not main_entries:
        return err(-1, "压缩包中未找到 plugin.so（请用 scripts/build_plugin.sh 打包）")
    main = min(main_entries, key=lambda i: i.filename.count("/"))
    prefix = main.filename[: -len(main.filename.split("/")[-1])]  # 含尾随 /

    # 插件名 = zip 内目录名（元信息以插件类 meta 为准）
    default_name = prefix.rstrip("/").rsplit("/", 1)[-1] if prefix \
        else (file.filename or "plugin").rsplit(".", 1)[0]
    name = default_name
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", name):
        return err(-1, f"插件名不合法：{name}（需小写字母开头，仅含小写字母/数字/下划线）")

    # 先解包到暂存目录，校验通过后原子替换（zip-slip 防护：越界路径直接拒绝）
    # 彻底废除 info.json：包内残留的 info.json 一律忽略，不落盘
    upload_root = PluginRegistry.UPLOAD_ROOT
    upload_root.mkdir(parents=True, exist_ok=True)
    stage = upload_root.parent / f".upload_{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        for i in entries:
            if not i.filename.startswith(prefix):
                continue
            target = stage / i.filename[len(prefix):]
            if target.name == "info.json":
                continue
            if not target.resolve().is_relative_to(stage.resolve()):
                return err(-1, "压缩包包含非法路径")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(i))
        # 包签名校验：编译镜像强制（源码运行可跳过）。必须在落盘前完成，
        # 否则未签名的包会先进入 storage/plugins 再被删，期间仍可能被扫描加载
        if plugin_signing.is_enforced():
            staged_so = stage / main.filename[len(prefix):]
            try:
                plugin_signing.verify_plugin_dir(stage, staged_so, expect_name=name)
            except plugin_signing.PackageSignatureError as e:
                return err(-1, f"插件包签名校验失败：{e}（请用 scripts/build_plugin.sh 打包/签名）")

        target_dir = upload_root / name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        stage.rename(target_dir)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # 立即刷新注册表；导入失败（ABI 不符/坏包）不保留半成品
    PluginRegistry.reload()
    if PluginRegistry.get_payment_class(name) is None:
        # 对外只给一句抽象提示（不回显内部异常文本，避免把堆栈/路径暴露给前端）；
        # 具体原因记一条日志（注册表扫描时还有完整 traceback），供运维排障
        _log.warning("插件导入失败 name=%s reason=%s", name, PluginRegistry.plugin_load_error(name))
        shutil.rmtree(upload_root / name, ignore_errors=True)
        PluginRegistry.reload()
        return err(-1, "插件导入失败：请确认插件包与当前系统环境匹配（详见服务端日志）")

    # 元信息必须来自插件类 meta，且 meta.name 与 zip 目录名一致（防注册键错位）
    meta = PluginRegistry.plugin_meta(name)
    if meta is None:
        shutil.rmtree(upload_root / name, ignore_errors=True)
        PluginRegistry.reload()
        return err(-1, "插件缺少 meta 元信息（PluginMeta），无法安装")
    if meta.name != name:
        shutil.rmtree(upload_root / name, ignore_errors=True)
        PluginRegistry.reload()
        return err(-1, f"插件名不一致：meta.name={meta.name}，zip 目录名={name}")

    showname = meta.showname
    types = ",".join(meta.types)
    inputs = meta.inputs
    version = (str(meta.version or ""))[:16] or None

    # 同步 Plugin 表，让后台插件列表立即可见（plugin_sync 同款最小集）
    row = await session.get(Plugin, name)
    if row:
        row.showname = showname
        row.supported_types = types
        row.inputs_schema = inputs
        row.version = version
    else:
        session.add(Plugin(name=name, showname=showname, supported_types=types,
                           inputs_schema=inputs, version=version, is_enabled=True))
    await session.commit()
    # 提示只给运维看得懂的信息：插件名 + 版本（"其他 worker/副本自动生效"属于实现细节）
    return suc({"name": name, "showname": showname, "version": version or "",
                "note": f"{showname} {version or ''} 安装成功".replace("  ", " ").strip()})


# ============================================================
# 支付方式（调用值/显示名称/支持设备）
# ============================================================

@router.get("/types",
            dependencies=[Depends(AnyPermissionChecker(
                "payment_type.view", "channel.view", "channel_code.view"))])
async def type_list(session: SessionDep):
    """支付方式字典（id/code/名称/图标）。

    通道、通道编码页面都要用它渲染名称与图标；只授予对应模块权限的账号也应该能读，
    否则页面进去就被 403 顶到错误页。只回字典字段，不含费率等业务数据。
    """
    rows = (await session.exec(
        select(PaymentType).order_by(col(PaymentType.id))
    )).all()
    return suc([{
        "id": t.id, "code": t.code, "name": t.name,
        "device": t.device, "status": t.status,
        # 图标 URL（为空则不渲染，避免前端拼不存在的 /assets/icon/{code}.ico 产生 404）
        "icon": t.icon or "",
    } for t in rows])


@router.get("/types/stats", dependencies=[Depends(PermissionChecker("payment_type.view"))])
async def type_stats(session: SessionDep):
    """全部支付方式的今日/昨日收款（支付方式列表的今日收款列）。"""
    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)
    yesterday_start = today_start - timedelta(days=1)
    stats: dict[int, dict] = {}

    async def _collect(start: datetime, end: datetime, prefix: str):
        rows = (await session.exec(
            select(Order.payment_type_id, func.count(), func.coalesce(func.sum(Order.realmoney), 0))
            .where(col(Order.status) == 1, col(Order.order_type) != ORDER_TYPE_TEST,
                   col(Order.paid_at) >= start, col(Order.paid_at) < end)
            .group_by(Order.payment_type_id)
        )).all()
        for type_id, cnt, total in rows:
            if not type_id:
                continue
            stats.setdefault(type_id, {})[f"{prefix}_count"] = cnt
            stats.setdefault(type_id, {})[f"{prefix}_amount"] = str(Decimal(str(total)))

    await _collect(today_start, now, "today")
    await _collect(yesterday_start, today_start, "yesterday")
    return suc(stats)


class PaymentTypeRequest(BaseModel):
    """字段与老版 pay_type.php 表单一致：调用值/显示名称/支持设备，另加图标。"""
    code: str
    name: str
    device: int = 0
    # 图标 URL：内置图标库取值形如 /icons/pay/wxpay.svg；留空表示不显示图标
    icon: str = ""


def _normalize_icon(icon: str) -> str:
    """图标地址归一化：只接受站内路径（/...）或 http(s) 外链，其余视为未配置。"""
    value = (icon or "").strip()
    if not value:
        return ""
    if value.startswith("/") or value.startswith(("http://", "https://")):
        return value[:255]
    return ""


async def _check_type_unique(session, code: str, device: int,
                             exclude_id: Optional[int] = None) -> Optional[str]:
    """老版校验规则：调用值仅限英文数字；同一调用值+支持设备不能重复。

    返回错误消息；通过时返回 None。调用值即开放 API type 参数，
    数据库层面全局唯一，此处提前校验以返回友好提示。
    """
    if not re.fullmatch(r"[a-zA-Z0-9]+", code or ""):
        return "调用值不符合规则"

    async def _exists(where):
        query = select(PaymentType).where(where)
        if exclude_id:
            query = query.where(PaymentType.id != exclude_id)
        return bool((await session.exec(query)).first())

    if await _exists((PaymentType.code == code) & (PaymentType.device == device)):
        return "同一个调用值+支持设备不能重复"
    if await _exists(PaymentType.code == code):
        return "调用值已存在"
    return None


def _validate_type_payload(body: PaymentTypeRequest) -> Optional[str]:
    if not (body.code or "").strip() or not (body.name or "").strip():
        return "请确保各项不能为空！"
    if body.device not in (0, 1, 2):
        return "支持设备不合法"
    return None


@router.post("/types", dependencies=[Depends(PermissionChecker("payment_type.create"))])
async def type_create(body: PaymentTypeRequest, session: SessionDep):
    if msg := _validate_type_payload(body):
        return err(-1, msg)
    if msg := await _check_type_unique(session, body.code.strip(), body.device):
        return err(-1, msg)
    # 老版新增固定开启，状态由列表开关切换
    t = PaymentType(code=body.code.strip(), name=body.name.strip(),
                    device=body.device, icon=_normalize_icon(body.icon) or None,
                    status=1)
    session.add(t)
    await session.commit()
    await session.refresh(t)
    return suc({"id": t.id})


@router.put("/types/{type_id}", dependencies=[Depends(PermissionChecker("payment_type.update"))])
async def type_update(type_id: int, body: PaymentTypeRequest, session: SessionDep):
    t = await session.get(PaymentType, type_id)
    if not t:
        return err(404, "支付方式不存在")
    if msg := _validate_type_payload(body):
        return err(-1, msg)
    if msg := await _check_type_unique(session, body.code.strip(), body.device, exclude_id=type_id):
        return err(-1, msg)
    t.code = body.code.strip()
    t.name = body.name.strip()
    t.device = body.device  # 老版编辑不改状态，状态由列表开关切换
    t.icon = _normalize_icon(body.icon) or None
    session.add(t)
    await session.commit()
    return suc("success")


class PaymentTypeStatusRequest(BaseModel):
    status: int


@router.put("/types/{type_id}/status",
            dependencies=[Depends(PermissionChecker("payment_type.update"))])
async def type_set_status(type_id: int, body: PaymentTypeStatusRequest, session: SessionDep):
    """列表开关直接切换状态。"""
    t = await session.get(PaymentType, type_id)
    if not t:
        return err(404, "支付方式不存在")
    t.status = 1 if body.status == 1 else 0
    session.add(t)
    await session.commit()
    return suc("success")


@router.delete("/types/{type_id}", dependencies=[Depends(PermissionChecker("payment_type.delete"))])
async def type_delete(type_id: int, session: SessionDep):
    """删除支付方式（含系统自带）；仍被通道引用时拦截。"""
    t = await session.get(PaymentType, type_id)
    if not t:
        return err(404, "支付方式不存在")
    channels = (await session.exec(
        select(func.count()).select_from(Channel).where(Channel.payment_type_id == type_id)
    )).one()
    if channels:
        return err(-1, "删除失败，存在使用该支付方式的支付通道")
    await session.delete(t)
    await session.commit()
    return suc("success")


# ============================================================
# 通道
# ============================================================

# 凭证脱敏哨兵：列表/详情默认把密钥类字段替换为该值回显；
# 编辑提交时「值等于哨兵」表示不修改，服务端保留库内原值（前端无需改造）。
CONFIG_MASK = "******"
# 字段名命中这些片段即视为密钥类（与 crypto 的凭证掩码同口径，按子串匹配）
_SECRET_FIELD_HINTS = ("key", "secret", "password", "passwd", "token", "private")


def _is_secret_field(name: str) -> bool:
    lowered = str(name).lower()
    return any(hint in lowered for hint in _SECRET_FIELD_HINTS)


def _mask_config(config: dict | None) -> dict:
    """密钥类字段 → `******`；空值保持空（前端据此判断「未配置」）。"""
    return {
        key: (CONFIG_MASK if value not in (None, "") else value)
        if _is_secret_field(key) else value
        for key, value in (config or {}).items()
    }


def _merge_config(submitted: dict, stored: dict) -> dict:
    """提交值合并：哨兵值=沿用库内原值，其余以提交为准（缺失的键即删除）。"""
    merged: dict = {}
    for key, value in (submitted or {}).items():
        if isinstance(value, str) and value.strip() == CONFIG_MASK:
            if key in stored:
                merged[key] = stored[key]
            continue
        merged[key] = value
    return merged


def _dump_channel(c: Channel, reveal: bool = False) -> dict:
    config = decrypt_config(c.config)
    data = {
        "id": c.id,
        "name": c.name,
        "plugin_name": c.plugin_name,
        "payment_type_id": c.payment_type_id,
        "merchant_rate": str(c.merchant_rate),
        "cost_rate": str(c.cost_rate),
        "status": c.status,
        "mode": c.mode,
        "weight": c.weight,
        "min_amount": str(c.min_amount),
        "max_amount": str(c.max_amount),
        "day_limit": str(c.day_limit),
        "day_limit_count": c.day_limit_count,
        "sort": c.sort,
        "debug_log": 1 if c.debug_log else 0,
        # 默认脱敏：channel.view（只读）拿不到上游 appkey/appsecret 明文
        "config": config if reveal else _mask_config(config),
    }
    return data


async def _get_plugin_inputs(session, plugin_name: str) -> list[dict]:
    # 进程内 meta 优先（改插件表单字段后立即生效）；库内快照兜底
    meta = PluginRegistry.plugin_meta(plugin_name)
    if meta is not None:
        return _normalize_inputs(meta.inputs)
    row = await session.get(Plugin, plugin_name)
    if row and row.inputs_schema:
        return _normalize_inputs(row.inputs_schema)
    return []


@router.get("/channels", dependencies=[Depends(PermissionChecker("channel.view"))])
async def channel_list(session: SessionDep, page: int = Query(1, ge=1),
                       per_page: int = Query(20, ge=1, le=100),
                       payment_type_id: Optional[int] = Query(None),
                       plugin_name: Optional[str] = Query(None)):
    query = select(Channel).order_by(col(Channel.id).desc())
    if payment_type_id:
        query = query.where(Channel.payment_type_id == payment_type_id)
    if plugin_name:
        query = query.where(Channel.plugin_name == plugin_name)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.exec(count_query)).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    return suc(paginate(
        items=[_dump_channel(c) for c in items], total=total,
        page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
    ))


@router.get("/channels/stats", dependencies=[Depends(PermissionChecker("channel.view"))])
async def channel_stats(session: SessionDep):
    """全部通道的今日/昨日收款（支付通道列表的收款统计列）。"""
    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)
    yesterday_start = today_start - timedelta(days=1)
    stats: dict[int, dict] = {}

    async def _collect(start: datetime, end: datetime, prefix: str):
        rows = (await session.exec(
            select(Order.channel_id, func.count(), func.coalesce(func.sum(Order.realmoney), 0))
            .where(col(Order.status) == 1, col(Order.order_type) != ORDER_TYPE_TEST,
                   col(Order.paid_at) >= start, col(Order.paid_at) < end)
            .group_by(Order.channel_id)
        )).all()
        for channel_id, cnt, total in rows:
            if not channel_id:
                continue
            stats.setdefault(channel_id, {})[f"{prefix}_count"] = cnt
            stats.setdefault(channel_id, {})[f"{prefix}_amount"] = str(Decimal(str(total)))

    await _collect(today_start, now, "today")
    await _collect(yesterday_start, today_start, "yesterday")
    return suc(stats)


@router.get("/channels/{channel_id}", dependencies=[Depends(PermissionChecker("channel.view"))])
async def channel_detail(channel_id: int, session: SessionDep, auth: AdminUser,
                         reveal: int = Query(0)):
    """通道详情。默认脱敏；`reveal=1` 且具备 channel.update 才回显明文凭证。"""
    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    can_reveal = bool(reveal) and await check_permission(auth, "channel.update", session)
    data = _dump_channel(c, reveal=can_reveal)
    data["config_revealed"] = can_reveal
    data["inputs"] = await _get_plugin_inputs(session, c.plugin_name)
    return suc(data)


class ChannelRequest(BaseModel):
    name: str
    plugin_name: str
    payment_type_id: int
    merchant_rate: str = "0.00"
    cost_rate: str = "0.00"
    status: int = 1
    mode: int = 0
    weight: int = 1
    min_amount: str = "0.01"
    max_amount: str = "0.00"
    day_limit: str = "0.00"
    day_limit_count: int = 0
    sort: int = 0
    # None=不触碰（toggleStatus 等透传场景不静默关掉调试开关）
    debug_log: Optional[int] = None
    config: dict = {}


@router.post("/channels", dependencies=[Depends(PermissionChecker("channel.create"))])
async def channel_create(body: ChannelRequest, session: SessionDep,
                         request: Request, auth: AdminUser):
    if msg := _validate_channel_payload(body):
        return err(-1, msg)
    if not await session.get(PaymentType, body.payment_type_id):
        return err(-1, "支付方式不存在")
    c = Channel(
        name=body.name.strip(),
        plugin_name=body.plugin_name,
        payment_type_id=body.payment_type_id,
        merchant_rate=dmoney(body.merchant_rate),
        cost_rate=dmoney(body.cost_rate),
        status=0,  # 新增通道默认关闭，测通后在列表开关启用
        mode=body.mode,
        weight=body.weight,
        min_amount=dmoney(body.min_amount),
        max_amount=dmoney(body.max_amount),
        day_limit=dmoney(body.day_limit),
        day_limit_count=body.day_limit_count,
        sort=body.sort,
        debug_log=1 if body.debug_log == 1 else 0,
        # 新建时不存在「库内原值」：哨兵值视为空（防止把掩码写进凭证）
        config=encrypt_config(_merge_config(body.config, {})),
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return suc(_dump_channel(c))


def _validate_channel_payload(body: ChannelRequest) -> str:
    """更新与创建共用的引用校验（P2-20：更新也必须过一遍）。"""
    if not (body.name or "").strip():
        return "通道名称不能为空"
    if not PluginRegistry.get_payment_class(body.plugin_name):
        return "插件不存在或未加载"
    return ""


@router.put("/channels/{channel_id}", dependencies=[Depends(PermissionChecker("channel.update"))])
async def channel_update(channel_id: int, body: ChannelRequest, session: SessionDep,
                         request: Request, auth: AdminUser):
    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    if msg := _validate_channel_payload(body):
        return err(-1, msg)
    pt = await session.get(PaymentType, body.payment_type_id)
    if not pt:
        return err(-1, "支付方式不存在")

    c.name = body.name.strip()
    c.plugin_name = body.plugin_name
    c.payment_type_id = body.payment_type_id
    c.merchant_rate = dmoney(body.merchant_rate)
    c.cost_rate = dmoney(body.cost_rate)
    c.status = 1 if body.status == 1 else 0  # 状态归一化，避免前端传 2 落库后语义不明
    c.mode = body.mode
    c.weight = body.weight
    c.min_amount = dmoney(body.min_amount)
    c.max_amount = dmoney(body.max_amount)
    c.day_limit = dmoney(body.day_limit)
    c.day_limit_count = body.day_limit_count
    c.sort = body.sort
    if body.debug_log is not None:
        c.debug_log = 1 if body.debug_log == 1 else 0
    if body.config:
        # 凭证：提交什么存什么；哨兵值（前端未改动的密钥）沿用库内原值
        c.config = encrypt_config(_merge_config(body.config, decrypt_config(c.config)))
    session.add(c)
    await session.commit()
    return suc(_dump_channel(c))


class DebugLogRequest(BaseModel):
    enabled: bool


@router.put("/channels/{channel_id}/debug-log",
            dependencies=[Depends(PermissionChecker("channel.update"))])
async def channel_toggle_debug_log(channel_id: int, body: DebugLogRequest, session: SessionDep):
    """通道列表「调试日志」开关：只改该字段。

    不能复用 PUT /channels/{id}：那个接口要求全量字段，从列表透传会把
    费率/限额等未在列表展示的配置一起重写（默认值覆盖真实值）。
    """
    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    c.debug_log = 1 if body.enabled else 0
    session.add(c)
    await session.commit()
    return suc({"id": c.id, "debug_log": c.debug_log})


@router.delete("/channels/{channel_id}", dependencies=[Depends(PermissionChecker("channel.delete"))])
async def channel_delete(channel_id: int, session: SessionDep, request: Request,
                         auth: AdminUser):
    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    # 被下游通道编码引用时拒绝删除：删掉会让编码静默少一个成员（与插件同步「仍被引用则跳过」同思路）
    refs = (await session.exec(
        select(ChannelCode.code)
        .join(ChannelCodeItem, col(ChannelCodeItem.code_id) == col(ChannelCode.id))
        .where(col(ChannelCodeItem.channel_id) == channel_id)
        .order_by(col(ChannelCode.code))
    )).all()
    if refs:
        return err(-1, f"该通道仍被通道编码引用（{', '.join(refs)}），请先在编码里解绑")
    await session.delete(c)
    await session.commit()
    return suc("success")


# ============================================================
# 通道编码（编码 → 通道绑定）
# ============================================================
#
# 编码是给下游的对外契约：全局唯一命名空间、落库统一小写；
# mode 是**编码级**策略（与通道自身的 mode 无关）。
# 轮询（旧 mode=1）已下线：后台不再提供该策略，保存时拒绝；存量编码由迁移 0019 统一改为随机。
CODE_MODES = {0: "随机", 2: "权重"}
# 权重按**百分比**配置：同一编码内各成员权重合计必须是 100，权重值本身就是分流占比
# （与 epay 后台「通道:权重」的口径一致，运营不用再拿 1/3 这类相对值心算）。
CODE_WEIGHT_SUM = 100
CODE_WEIGHT_MAX = 100


class ChannelCodeItemRequest(BaseModel):
    channel_id: int
    weight: int = 1
    sort: int = 0


class ChannelCodeRequest(BaseModel):
    code: str
    name: str = ""
    remark: str = ""
    mode: int = 0
    status: int = 1
    # 测试编码：商户端「对接信息」只展示这条（允许多条，取 id 最小的一条启用编码）
    is_test: bool = False
    # 编码级单笔金额区间（两端含，0=不限，两边都 0=不限制）
    min_amount: str = "0.00"
    max_amount: str = "0.00"
    # 编码级商户费率（%）：空=不覆盖（走商户专属 / 通道费率）
    fee_rate: str = ""
    items: list[ChannelCodeItemRequest] = []


class ChannelCodeStatusRequest(BaseModel):
    status: int


def _parse_code_limits(body: ChannelCodeRequest) -> tuple[Decimal, Decimal, Decimal | None, str]:
    """解析并校验编码级限额/费率。返回 (min, max, fee_rate|None, 错误文案)。"""
    try:
        minv = dmoney(body.min_amount or "0.00")
        maxv = dmoney(body.max_amount or "0.00")
    except Exception:
        return Decimal("0"), Decimal("0"), None, "金额限额格式错误"
    if minv < 0 or maxv < 0:
        return minv, maxv, None, "金额限额不能为负数"
    # max=0 表示不限上限；min=0 表示不限下限，两者都 0 = 不限制
    if maxv > 0 and minv > maxv:
        return minv, maxv, None, f"金额下限不能大于上限（{minv} > {maxv}）"
    fee = None
    raw = (body.fee_rate or "").strip()
    if raw:
        try:
            fee = dmoney(raw)
        except Exception:
            return minv, maxv, None, "费率格式错误"
        if fee < 0 or fee > 100:
            return minv, maxv, None, f"费率需在 0~100 之间（当前 {fee}）"
    return minv, maxv, fee, ""


async def _validate_code_payload(session, body: ChannelCodeRequest,
                                 exclude_id: int = 0) -> tuple[str, str]:
    """校验编码主档 + 成员。返回 (归一后的 code, 错误文案)；错误文案为空表示通过。"""
    code = normalize_channel_code(body.code)
    if not code:
        return code, "通道编码不能为空"
    if not code_format_ok(code):
        return code, "通道编码仅支持 1~32 位字母、数字、- 和 _"
    if body.mode == 1:
        return code, "轮询策略已下线，请选择随机或权重"
    if body.mode not in CODE_MODES:
        return code, "策略取值不合法"
    _, _, _, limit_error = _parse_code_limits(body)
    if limit_error:
        return code, limit_error
    dup = await get_channel_code(session, code)
    if dup and dup.id != exclude_id:
        return code, "通道编码已存在"
    if not body.items:
        return code, "通道编码至少绑定一个通道"
    channel_ids = [it.channel_id for it in body.items]
    if len(set(channel_ids)) != len(channel_ids):
        return code, "同一个通道不能重复绑定"
    existing = set((await session.exec(
        select(col(Channel.id)).where(col(Channel.id).in_(channel_ids))
    )).all())
    missing = [cid for cid in channel_ids if cid not in existing]
    if missing:
        return code, f"通道不存在：{', '.join(str(c) for c in missing)}"
    for it in body.items:
        if it.weight < 0:
            return code, "权重不能为负数"
        if it.weight > CODE_WEIGHT_MAX:
            return code, f"权重不能超过 {CODE_WEIGHT_MAX}（权重按百分比配置）"
    # 权重按百分比配置：合计必须 100，权重值即占比，运营一眼能核对。
    # 全零同样落在这里（合计 0），单独给一句更具体的文案。
    if body.mode == 2:
        total = sum(it.weight for it in body.items)
        if total == 0:
            return code, "权重模式下至少一个通道的权重要大于 0（各通道权重合计需为 100）"
        if total != CODE_WEIGHT_SUM:
            return code, f"权重合计必须等于 {CODE_WEIGHT_SUM}（当前 {total}）"
    return code, ""


async def _replace_code_items(session, code_id: int, items: list[ChannelCodeItemRequest]) -> None:
    """成员整表覆盖写：没提交的绑定即删除。"""
    old = (await session.exec(
        select(ChannelCodeItem).where(col(ChannelCodeItem.code_id) == code_id)
    )).all()
    old_map = {it.channel_id: it for it in old}
    keep = {it.channel_id for it in items}
    for channel_id, row in old_map.items():
        if channel_id not in keep:
            await session.delete(row)
    for idx, it in enumerate(items):
        row = old_map.get(it.channel_id)
        if row is None:
            row = ChannelCodeItem(code_id=code_id, channel_id=it.channel_id)
        row.weight = it.weight
        row.sort = it.sort if it.sort else idx
        session.add(row)


def _dump_code_item(item: ChannelCodeItem, c: Channel | None) -> dict:
    return {
        "channel_id": item.channel_id,
        "weight": item.weight,
        "sort": item.sort,
        "channel_name": c.name if c else "（通道已删除）",
        "plugin_name": c.plugin_name if c else "",
        "payment_type_id": c.payment_type_id if c else 0,
        "channel_status": c.status if c else 0,
        # 商户费率随成员带出：编码模块要能一眼看到这个通道对商户抽多少
        # （成本费率仍只在通道页，不在编码模块暴露）
        "merchant_rate": str(c.merchant_rate) if c else "0.00",
    }


async def _code_today_stats(session) -> dict[int, dict]:
    """按编码聚合今日/昨日已支付笔数与金额（口径与通道统计一致：排除测试单）。"""
    now = datetime.now()
    today_start = datetime.combine(now.date(), time.min)
    yesterday_start = today_start - timedelta(days=1)
    stats: dict[int, dict] = {}

    async def _collect(start: datetime, end: datetime, prefix: str):
        rows = (await session.exec(
            select(Order.channel_code_id, func.count(), func.coalesce(func.sum(Order.realmoney), 0))
            .where(col(Order.status) == 1, col(Order.order_type) != ORDER_TYPE_TEST,
                   col(Order.paid_at) >= start, col(Order.paid_at) < end,
                   col(Order.channel_code_id) > 0)
            .group_by(Order.channel_code_id)
        )).all()
        for code_id, cnt, total in rows:
            stats.setdefault(code_id, {})[f"{prefix}_count"] = cnt
            stats.setdefault(code_id, {})[f"{prefix}_amount"] = str(Decimal(str(total)))

    await _collect(today_start, now, "today")
    await _collect(yesterday_start, today_start, "yesterday")
    return stats


@router.get("/channel-options",
            dependencies=[Depends(AnyPermissionChecker("channel.view", "channel_code.view"))])
async def channel_options(session: SessionDep):
    """通道编码弹窗的通道候选（只回挑通道要用的字段）。

    持有 `channel_code.view` 但没有 `channel.view` 的账号也能配置编码。回商户费率是
    因为编码模块要展示「这条编码下的通道各抽多少」；成本费率与凭证仍不出这个接口。
    """
    rows = (await session.exec(
        select(Channel).order_by(col(Channel.sort), col(Channel.id))
    )).all()
    return suc([{
        "id": c.id,
        "name": c.name,
        "plugin_name": c.plugin_name,
        "payment_type_id": c.payment_type_id,
        "status": c.status,
        "merchant_rate": str(c.merchant_rate),
    } for c in rows])


@router.get("/channel-codes", dependencies=[Depends(PermissionChecker("channel_code.view"))])
async def channel_code_list(session: SessionDep, page: int = Query(1, ge=1),
                            per_page: int = Query(20, ge=1, le=100),
                            status: Optional[int] = Query(None),
                            keyword: str = ""):
    query = select(ChannelCode).order_by(col(ChannelCode.id).desc())
    if status is not None:
        query = query.where(col(ChannelCode.status) == status)
    kw = normalize_channel_code(keyword)
    if kw:
        query = query.where(
            (col(ChannelCode.code).contains(kw)) | (col(ChannelCode.name).contains(keyword.strip()))
        )
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.exec(count_query)).one()
    rows = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    stats = await _code_today_stats(session)

    code_ids = [c.id for c in rows]
    item_map: dict[int, list[ChannelCodeItem]] = {}
    channel_map: dict[int, Channel] = {}
    if code_ids:
        items = (await session.exec(
            select(ChannelCodeItem).where(col(ChannelCodeItem.code_id).in_(code_ids))
        )).all()
        for it in items:
            item_map.setdefault(it.code_id, []).append(it)
        ch_ids = {it.channel_id for it in items}
        if ch_ids:
            for c in (await session.exec(select(Channel).where(col(Channel.id).in_(ch_ids)))).all():
                channel_map[c.id] = c

    data = []
    for c in rows:
        members = item_map.get(c.id, [])
        data.append({
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "remark": c.remark or "",
            "mode": c.mode,
            "status": c.status,
            "is_test": bool(c.is_test),
            "min_amount": str(c.min_amount),
            "max_amount": str(c.max_amount),
            "amount_text": code_amount_text(c),
            "fee_rate": str(c.fee_rate) if c.fee_rate is not None else "",
            "item_count": len(members),
            "members": [
                _dump_code_item(it, channel_map.get(it.channel_id)) for it in members
            ],
            **(stats.get(c.id) or {}),
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
        })
    return suc(paginate(
        items=data, total=total, page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
    ))


@router.get("/channel-codes/{code_id}", dependencies=[Depends(PermissionChecker("channel_code.view"))])
async def channel_code_detail(code_id: int, session: SessionDep):
    c = await session.get(ChannelCode, code_id)
    if not c:
        return err(404, "通道编码不存在")
    items = (await session.exec(
        select(ChannelCodeItem).where(col(ChannelCodeItem.code_id) == code_id)
        .order_by(col(ChannelCodeItem.sort), col(ChannelCodeItem.id))
    )).all()
    ch_ids = {it.channel_id for it in items}
    channels = {}
    if ch_ids:
        for ch in (await session.exec(select(Channel).where(col(Channel.id).in_(ch_ids)))).all():
            channels[ch.id] = ch
    return suc({
        "id": c.id,
        "code": c.code,
        "name": c.name,
        "remark": c.remark or "",
        "mode": c.mode,
        "status": c.status,
        "is_test": bool(c.is_test),
        "min_amount": str(c.min_amount),
        "max_amount": str(c.max_amount),
        "amount_text": code_amount_text(c),
        "fee_rate": str(c.fee_rate) if c.fee_rate is not None else "",
        "items": [_dump_code_item(it, channels.get(it.channel_id)) for it in items],
    })


@router.post("/channel-codes", dependencies=[Depends(PermissionChecker("channel_code.create"))])
async def channel_code_create(body: ChannelCodeRequest, session: SessionDep):
    code, msg = await _validate_code_payload(session, body)
    if msg:
        return err(-1, msg)
    minv, maxv, fee, _ = _parse_code_limits(body)
    c = ChannelCode(
        code=code,
        name=(body.name or "").strip()[:64] or code,
        remark=(body.remark or "").strip()[:255] or None,
        mode=body.mode,
        status=1 if body.status == 1 else 0,
        is_test=bool(body.is_test),
        min_amount=minv,
        max_amount=maxv,
        fee_rate=fee,
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    await _replace_code_items(session, c.id, body.items)
    await session.commit()
    return suc({"id": c.id, "code": c.code})


@router.put("/channel-codes/{code_id}", dependencies=[Depends(PermissionChecker("channel_code.update"))])
async def channel_code_update(code_id: int, body: ChannelCodeRequest, session: SessionDep):
    c = await session.get(ChannelCode, code_id)
    if not c:
        return err(404, "通道编码不存在")
    code, msg = await _validate_code_payload(session, body, exclude_id=code_id)
    if msg:
        return err(-1, msg)
    minv, maxv, fee, _ = _parse_code_limits(body)
    c.code = code
    c.name = (body.name or "").strip()[:64] or code
    c.remark = (body.remark or "").strip()[:255] or None
    c.mode = body.mode
    c.status = 1 if body.status == 1 else 0
    c.is_test = bool(body.is_test)
    c.min_amount = minv
    c.max_amount = maxv
    c.fee_rate = fee
    session.add(c)
    await _replace_code_items(session, code_id, body.items)
    await session.commit()
    return suc({"id": c.id, "code": c.code})


@router.put("/channel-codes/{code_id}/status",
            dependencies=[Depends(PermissionChecker("channel_code.update"))])
async def channel_code_set_status(code_id: int, body: ChannelCodeStatusRequest,
                                  session: SessionDep):
    """编码总闸：只改开关，不触碰成员与权重。"""
    c = await session.get(ChannelCode, code_id)
    if not c:
        return err(404, "通道编码不存在")
    c.status = 1 if body.status == 1 else 0
    session.add(c)
    await session.commit()
    return suc({"id": c.id, "status": c.status})


@router.delete("/channel-codes/{code_id}",
               dependencies=[Depends(PermissionChecker("channel_code.delete"))])
async def channel_code_delete(code_id: int, session: SessionDep):
    """删除编码与全部绑定。历史订单保留 channel_code 字符串作为留痕，不回改。

    仍有待支付订单引用时拒绝删除：收银台按订单落的 `channel_code_id` 选通道，
    编码被删会让这些订单在收银台里既看不到支付方式、又误报「不支持当前支付方式」。
    """
    c = await session.get(ChannelCode, code_id)
    if not c:
        return err(404, "通道编码不存在")
    pending = (await session.exec(
        select(func.count()).select_from(Order).where(
            col(Order.channel_code_id) == code_id,
            col(Order.status) == ORDER_PENDING,
        )
    )).one()
    if int(pending) > 0:
        return err(-1, f"该编码仍有 {int(pending)} 笔待支付订单引用，请先处理这些订单，或改为停用编码")
    for it in (await session.exec(
        select(ChannelCodeItem).where(col(ChannelCodeItem.code_id) == code_id)
    )).all():
        await session.delete(it)
    await session.delete(c)
    await session.commit()
    return suc("success")


# ============================================================
# 通道测试支付（支付通道列表的「测试」）
# ============================================================

TEST_PAY_MIN = "0.01"
TEST_PAY_MAX = "1000.00"


def _test_debug_payload(c: Channel, order, plugin=None, client_ip: str = "",
                        error: str | None = None) -> dict:
    """通道测试的调试信息：通道/测试请求/上游请求响应快照，失败排障用。

    - 凭证类字段一律脱敏：调试弹窗不需要、也不应携带上游 appkey
    - 测试单已落库（order_type=2），这里带出真实单号，便于跳订单页核对
    """
    cfg = decrypt_config(c.config)
    plugin_debug = getattr(plugin, "last_debug", None) if plugin else None
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "client_ip": client_ip,
        "channel": {
            "id": c.id,
            "name": c.name,
            "plugin": c.plugin_name,
            "gateway": cfg.get("appurl") or "（默认网关）",
            "merchant": cfg.get("appmchid", ""),
            "appkey": CONFIG_MASK if cfg.get("appkey") else "",
            "productid": cfg.get("productid", ""),
        },
        "request": {
            "trade_no": order.trade_no if order else "",
            "amount": str(order.money) if order else "",
            "payment_type": order.payment_type_code if order else "",
        },
        "upstream_request": plugin_debug,
        "error": error,
    }


class ChannelTestPayRequest(BaseModel):
    name: str = "支付测试"
    amount: str = "1.00"


@router.post("/channels/{channel_id}/testpay",
             dependencies=[Depends(PermissionChecker("channel.update"))])
async def channel_testpay(channel_id: int, body: ChannelTestPayRequest, request: Request,
                          session: SessionDep, cache: CacheDep, admin: AdminUser):
    """通道测试：建一笔测试单（order_type=2）并用该通道凭证向上游下单。

    链路与正式收款一致（落单 → 上游下单 → 回调验签 → 订单置已支付），
    因此能验证插件回调；但测试单：
    - 挂在系统测试商户名下（自动创建，无需配置测试商户号）
    - 不入余额/分润/结算/统计，不占通道日限额与限速
    - 不投递商户通知
    未开启的通道也允许测试（先测通再启用）；金额放宽到 0.01~1000 元。
    """
    from app.core.config import config
    from app.services.order import create_test_order, process_payment
    from app.services.settings import get_all_settings
    from app.services.test_pay import get_test_merchant

    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    if not c.config or decrypt_config(c.config) == {}:
        return err(-1, "请先配置好通道凭证")

    try:
        amount = dmoney(body.amount)
    except Exception:
        return err(-1, "金额格式错误")
    if amount < Decimal(TEST_PAY_MIN) or amount > Decimal(TEST_PAY_MAX):
        return err(-1, f"测试金额需在 {TEST_PAY_MIN} ~ {TEST_PAY_MAX} 元之间")

    pt = await session.get(PaymentType, c.payment_type_id)
    if not pt or pt.status != 1:
        return err(-1, "通道的支付方式不可用")

    settings = await get_all_settings(session, cache)
    site = (settings.get("site_url") or str(request.base_url)).rstrip("/")

    merchant = await get_test_merchant(session)
    # 支付完成回跳后台订单页：回调会追加 ?trade_no=，HashRouter 会按路由参数打开详情列表
    return_url = f"{site}/{config.ADMIN_PATH_PREFIX}/#/trade/orders"
    debug_ip = request.client.host if request.client else ""
    order = await create_test_order(
        session,
        merchant=merchant,
        payment_type=pt,
        money=amount,
        subject=body.name or "支付测试",
        client_ip=debug_ip,
        return_url=return_url,
        cache=cache,
    )

    flow = await process_payment(
        order, merchant, session, cache,
        notify_base=site, return_base=site, site_url=site,
        channel_id=c.id,
    )
    if not flow.ok:
        return err(-1, flow.error or "获取支付接口数据失败", errors=[
            _test_debug_payload(c, order, flow.plugin, debug_ip,
                                error=flow.error or "获取支付接口数据失败"),
        ])

    debug = _test_debug_payload(c, order, flow.plugin, debug_ip)
    return suc({
        "trade_no": order.trade_no,
        "pay_type": flow.pay_type,
        "pay_info": flow.pay_info,
        "debug": debug,
    })
