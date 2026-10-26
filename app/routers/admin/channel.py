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
from app.core.security import AdminUser, PermissionChecker
from app.lib.linktoken import cashier_url as build_cashier_url
from app.models import Channel, Merchant, Order, PaymentType, Plugin
from app.responses import err, paginate, suc
from app.services.balance import dmoney
from app.services.crypto import decrypt_config, encrypt_config
from app.services.plugin import PluginRegistry
from app.services.plugin_interfaces import PluginMeta

router = APIRouter()


# ============================================================
# 插件
# ============================================================

@router.get("/plugins", dependencies=[Depends(PermissionChecker("channel.view"))])
async def plugin_list(session: SessionDep):
    # Plugin 主键为 name（无自增 id），按名称排序
    rows = (await session.exec(select(Plugin).order_by(col(Plugin.name)))).all()
    return suc([{
        "name": p.name,
        "showname": p.showname,
        "supported_types": (p.supported_types or "").split(",") if p.supported_types else [],
        "inputs": p.inputs_schema or [],
        "is_enabled": bool(p.is_enabled),
        "version": p.version or "",
        # 插件来源：builtin=镜像内置，third=三方（storage/plugins）；行已失效时兜底 unknown
        "source": PluginRegistry.plugin_source(p.name) or "unknown",
    } for p in rows])


@router.post("/plugins/sync", dependencies=[Depends(PermissionChecker("channel.update"))])
async def plugin_sync(session: SessionDep):
    """扫描 plugins/ 目录刷新插件注册表；盘上已不存在的插件行一并清理
    （仍被通道引用的跳过，避免通道静默失效，提示人工处理）。"""
    PluginRegistry.reload()
    metas = PluginRegistry.list_plugins()
    names = {m["name"] for m in metas}
    for meta in metas:
        row = await session.get(Plugin, meta["name"])
        inputs = meta.get("inputs", [])
        version = (meta.get("version") or "")[:16] or None
        if row:
            row.showname = meta["showname"]
            row.supported_types = ",".join(meta["types"])
            row.inputs_schema = inputs
            row.version = version
        else:
            session.add(Plugin(
                name=meta["name"],
                showname=meta["showname"],
                supported_types=",".join(meta["types"]),
                inputs_schema=inputs,
                version=version,
                is_enabled=True,
            ))
    # 清理：注册表中已不存在的插件行；仍被通道引用的跳过并提示
    removed, referenced = [], []
    for row in (await session.exec(select(Plugin))).all():
        if row.name in names:
            continue
        used = (await session.exec(
            select(func.count()).select_from(Channel).where(Channel.plugin_name == row.name)
        )).one()
        if used:
            referenced.append(row.name)
            continue
        await session.delete(row)
        removed.append(row.name)
    await session.commit()
    msg = f"已同步 {len(metas)} 个插件"
    if removed:
        msg += f"，清理失效插件 {len(removed)} 个"
    if referenced:
        msg += f"；{len(referenced)} 个插件仍被通道引用未删除：{','.join(referenced)}"
    return suc(msg)


# 上传插件包大小上限
_UPLOAD_MAX_SIZE = 10 * 1024 * 1024


@router.post("/plugins/upload", dependencies=[Depends(PermissionChecker("channel.update"))])
async def plugin_upload(file: UploadFile, session: SessionDep):
    """上传安装编译后的插件包（zip）。

    包结构 = scripts/build_plugin.sh 产物目录直接压缩（通常只有主模块一个文件）：
      <插件名>/plugin.cpython-312-x86_64-linux-gnu.so
    仅接受 .so 编译产物（含 .py 一律拒绝，与镜像源码保护口径一致）；插件名取
    zip 内目录名，元信息/版本号来自插件类的 meta（不再支持 info.json，包内残留的
    info.json 会被忽略）。安装到 storage/plugins/<插件名>/（持久卷随 storage 备份，
    容器重建不丢），安装后立即刷新注册表并同步 Plugin 表。
    多 worker/多副本部署时其他进程需重启生效。
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

    # 同步 Plugin 表，让「已安装插件」列表立即可见（plugin_sync 同款最小集）
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
                "note": "多进程/多副本部署时，其他进程需重启后生效"})


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

def _dump_channel(c: Channel, with_config: bool = False) -> dict:
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
        "config": decrypt_config(c.config),
    }
    return data


async def _get_plugin_inputs(session, plugin_name: str) -> list[dict]:
    row = await session.get(Plugin, plugin_name)
    if row and row.inputs_schema:
        return row.inputs_schema
    meta = PluginRegistry.plugin_meta(plugin_name)
    return meta.inputs if meta else []


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
async def channel_detail(channel_id: int, session: SessionDep):
    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    data = _dump_channel(c)
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
async def channel_create(body: ChannelRequest, session: SessionDep):
    plugin = PluginRegistry.get_payment_class(body.plugin_name)
    if not plugin:
        return err(-1, "插件不存在或未加载")
    c = Channel(
        name=body.name,
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
        config=encrypt_config(body.config),
    )
    session.add(c)
    await session.commit()
    await session.refresh(c)
    return suc(_dump_channel(c))


@router.put("/channels/{channel_id}", dependencies=[Depends(PermissionChecker("channel.update"))])
async def channel_update(channel_id: int, body: ChannelRequest, session: SessionDep):
    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    c.name = body.name
    c.plugin_name = body.plugin_name
    c.payment_type_id = body.payment_type_id
    c.merchant_rate = dmoney(body.merchant_rate)
    c.cost_rate = dmoney(body.cost_rate)
    c.status = body.status
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
        # 凭证明文回显、直接编辑：提交什么就存什么（空 dict 表示不触碰，供开关等场景透传）
        c.config = encrypt_config(body.config)
    session.add(c)
    await session.commit()
    return suc(_dump_channel(c))


@router.delete("/channels/{channel_id}", dependencies=[Depends(PermissionChecker("channel.delete"))])
async def channel_delete(channel_id: int, session: SessionDep):
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


async def _test_debug_payload(c: Channel, order, flow, client_ip: str) -> dict:
    """通道测试的调试信息：通道/订单/上游请求响应快照，失败排障用。"""
    from app.services.crypto import decrypt_config

    cfg = decrypt_config(c.config)
    plugin_debug = getattr(flow.plugin, "last_debug", None) if flow.plugin else None
    return {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "client_ip": client_ip,
        "channel": {
            "id": c.id,
            "name": c.name,
            "plugin": c.plugin_name,
            "gateway": cfg.get("appurl") or "（默认网关）",
            "merchant": cfg.get("appmchid", ""),
            "appkey": str(cfg.get("appkey", "")),
            "productid": cfg.get("productid", ""),
        },
        "order": {
            "trade_no": order.trade_no,
            "amount": str(order.money),
            "realmoney": str(order.realmoney or ""),
        },
        "upstream_request": plugin_debug,
        "error": flow.error,
    }


class ChannelTestPayRequest(BaseModel):
    name: str = "支付测试"
    amount: str = "1.00"


@router.post("/channels/{channel_id}/testpay",
             dependencies=[Depends(PermissionChecker("channel.update"))])
async def channel_testpay(channel_id: int, body: ChannelTestPayRequest, request: Request,
                          session: SessionDep, cache: CacheDep, admin: AdminUser):
    """通道测试：在测试商户名下建一笔指定通道的订单，返回收银台地址。

    未开启的通道也允许测试（先测通再启用）；金额放宽到 0.01~1000 元。
    """
    from app.services.order import create_order, process_payment
    from app.services.settings import get_all_settings, get_int_setting, get_setting

    c = await session.get(Channel, channel_id)
    if not c:
        return err(404, "通道不存在")
    if not c.config or decrypt_config(c.config) == {}:
        return err(-1, "请先配置好通道凭证")

    if not bool(await get_setting(session, "test_pay_enable", True)):
        return err(-1, "测试支付未开启，请先在站点配置中开启")
    test_pid = await get_int_setting(session, "test_pay_merchant_pid")
    if test_pid <= 0:
        return err(-1, "请先在站点配置中配置测试商户PID")
    test_merchant = (await session.exec(
        select(Merchant).where(Merchant.pid == test_pid, Merchant.status == 1)
    )).first()
    if not test_merchant:
        return err(-1, "测试商户不可用，请检查测试商户PID配置")

    try:
        amount = dmoney(body.amount)
    except Exception:
        return err(-1, "金额格式错误")
    if amount < Decimal(TEST_PAY_MIN) or amount > Decimal(TEST_PAY_MAX):
        return err(-1, f"测试金额需在 {TEST_PAY_MIN} ~ {TEST_PAY_MAX} 元之间")

    pt = await session.get(PaymentType, c.payment_type_id)
    if not pt or pt.status != 1:
        return err(-1, "通道的支付方式不可用")

    import random
    out_trade_no = f"CT{datetime.now().strftime('%y%m%d%H%M%S')}{random.randint(100000, 999999)}"
    try:
        order = await create_order(
            {
                "pid": test_pid,
                "out_trade_no": out_trade_no,
                "name": body.name or "支付测试",
                "money": str(amount),
                "type": pt.code,
                "notify_url": "",
                "return_url": "",
                "client_ip": request.client.host if request.client else "",
            },
            session, cache, order_type=0,
        )
    except ValueError as e:
        return err(-1, str(e))

    settings = await get_all_settings(session, cache)
    site = (settings.get("site_url") or str(request.base_url)).rstrip("/")
    flow = await process_payment(order, test_merchant, session, cache, site_url=site,
                                 notify_base=site, return_base=site, channel_id=channel_id)
    debug = await _test_debug_payload(c, order, flow,
                                      request.client.host if request.client else "")
    if not flow.ok:
        # errors[0] 携带调试信息（前端测试失败弹窗展示）
        return err(-1, flow.error or "拉起支付失败", errors=[debug])
    return suc({
        "trade_no": order.trade_no,
        "cashier_url": build_cashier_url(site, order.trade_no),
        "pay_type": flow.pay_type,
        "pay_info": flow.pay_info,
        "debug": debug,
    })
