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
from app.core.security import AdminUser, PermissionChecker, check_permission, require_super_admin
from app.models import Channel, Order, PaymentType, Plugin
from app.responses import err, paginate, suc
from app.services.balance import dmoney
from app.services.crypto import decrypt_config, encrypt_config
from app.services.plugin import PluginRegistry
from app.services.plugin_interfaces import PluginMeta
from app.services.plugin_sync import sync_plugins

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


@router.post("/plugins/upload", dependencies=[Depends(require_super_admin)])
async def plugin_upload(file: UploadFile, session: SessionDep, request: Request,
                        auth: AdminUser):
    """上传安装编译后的插件包（zip）。**仅超管**：装插件 = 进程内执行 .so。

    包结构 = scripts/build_plugin.sh 产物目录直接压缩（通常只有主模块一个文件）：
      <插件名>/plugin.cpython-312-x86_64-linux-gnu.so
    仅接受 .so 编译产物（含 .py 一律拒绝，与镜像源码保护口径一致）；插件名取
    zip 内目录名，元信息/版本号来自插件类的 meta（不再支持 info.json，包内残留的
    info.json 会被忽略）。安装到 storage/plugins/<插件名>/（持久卷随 storage 备份，
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
        target_dir = upload_root / name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        stage.rename(target_dir)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # 立即刷新注册表；导入失败（ABI 不符/坏包）不保留半成品
    PluginRegistry.reload()
    if PluginRegistry.get_payment_class(name) is None:
        shutil.rmtree(upload_root / name, ignore_errors=True)
        PluginRegistry.reload()
        return err(-1, "插件导入失败：.so 与运行环境不兼容或缺少 BasePayment 子类")

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
    return suc({"name": name, "showname": showname,
                "note": "安装成功；其他 worker/副本随后自动生效，后台刷新即可看到"})


# ============================================================
# 支付方式（调用值/显示名称/支持设备）
# ============================================================

@router.get("/types", dependencies=[Depends(PermissionChecker("payment_type.view"))])
async def type_list(session: SessionDep):
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
            .where(col(Order.status) == 1, col(Order.paid_at) >= start, col(Order.paid_at) < end)
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
    """字段与老版 pay_type.php 表单一致：调用值/显示名称/支持设备。"""
    code: str
    name: str
    device: int = 0


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
                    device=body.device, status=1)
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
    if type_id < 4:
        return err(-1, "系统自带支付方式暂不支持删除")
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
            .where(col(Order.status) == 1, col(Order.paid_at) >= start, col(Order.paid_at) < end)
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
    await session.delete(c)
    await session.commit()
    return suc("success")


# ============================================================
# 通道测试支付（支付通道列表的「测试」）
# ============================================================

TEST_PAY_MIN = "0.01"
TEST_PAY_MAX = "1000.00"


def _test_debug_payload(c: Channel, ctx, plugin=None, client_ip: str = "",
                        error: str | None = None) -> dict:
    """通道测试的调试信息：通道/测试请求/上游请求响应快照，失败排障用。

    - 凭证类字段一律脱敏：调试弹窗不需要、也不应携带上游 appkey
    - 测试不落订单、不涉及商户，这里只是插件 submit 用的模拟请求参数
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
            "trade_no": ctx.order.get("trade_no", ""),
            "amount": ctx.order.get("money", ""),
            "payment_type": ctx.order.get("payment_type_code", ""),
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
    """通道测试：用该通道自己的凭证直接向上游发起一笔下单，返回支付信息。

    只验证「凭证 + 插件协议」是否可用：
    - 不建订单、不涉及商户、不动余额/费率/统计
    - 未开启的通道也允许测试（先测通再启用）；金额放宽到 0.01~1000 元
    """
    from app.services.plugin import maybe_await
    from app.services.plugin_interfaces import PaymentContext
    from app.services.settings import get_all_settings

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

    import random
    test_no = f"TEST{datetime.now().strftime('%y%m%d%H%M%S')}{random.randint(100000, 999999)}"
    # 模拟一笔下单上下文：回调地址仍指向真实域名（部分上游会校验格式），
    # 但库里没有这笔订单，回调不会被受理 —— 测试只需要拿到支付信息。
    ctx = PaymentContext(
        order={
            "trade_no": test_no,
            "out_trade_no": test_no,
            "subject": body.name or "支付测试",
            "money": str(amount),
            "realmoney": str(amount),
            "payment_type_code": pt.code,
        },
        notify_url=f"{site}/callbacks/{c.plugin_name}/{test_no}",
        return_url=f"{site}/callbacks/{c.plugin_name}/{test_no}/return",
        site_url=site,
        client_ip=request.client.host if request.client else "",
    )

    plugin = PluginRegistry.get_payment(c.plugin_name, decrypt_config(c.config))
    if not plugin:
        return err(-1, f"支付插件 {c.plugin_name} 未安装")

    debug_ip = request.client.host if request.client else ""
    try:
        result = await maybe_await(plugin.submit(ctx))
    except Exception as e:
        import traceback
        return err(-1, "上游通道请求失败", errors=[
            _test_debug_payload(c, ctx, plugin, debug_ip, error=traceback.format_exc()),
        ])

    pay_info = result.url or result.html or ""
    debug = _test_debug_payload(c, ctx, plugin, debug_ip,
                                error=None if result.kind != "error" else result.msg)
    if result.kind == "error" or (result.kind != "jump" and not pay_info):
        return err(-1, result.msg or "获取支付接口数据失败", errors=[debug])

    return suc({
        "pay_type": result.kind,
        "pay_info": pay_info,
        "debug": debug,
    })
