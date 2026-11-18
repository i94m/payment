"""插件表同步 — 把插件注册表（磁盘扫描结果）落库到 `plugins` 表。

`GET /admin-api/pay/plugins` 以及「新增通道」的插件下拉都读这张表，
所以建库/迁移后必须同步一次，否则全新安装的界面插件计数为 0、
新增通道没有可选插件（B05）。

规则与后台「同步插件」按钮一致（幂等）：
- 按 name upsert：showname / supported_types / inputs_schema / version
- 盘上已不存在的插件行清理；仍被通道引用的跳过，避免通道静默失效

`sync_plugins` 内的 `PluginRegistry.reload()` 会写跨进程刷新戳：多 worker/
多副本部署下其他进程随后自动重扫，后台上传/同步后不需要重启容器
（后台插件列表刷新时会强制比对，一定返回盘上最新状态）。
"""
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Channel, Plugin
from app.services.plugin import PluginRegistry


async def sync_plugins(session: AsyncSession) -> dict:
    """扫描插件目录并同步 plugins 表，返回同步结果摘要。

    返回 {synced, added, removed, referenced, errors, message}：
    synced=注册表插件数，added=本次新增入库的插件数，removed=清理的失效插件名，
    referenced=仍被通道引用而未删除的插件名。
    """
    PluginRegistry.reload()
    metas = PluginRegistry.list_plugins()
    names = {m["name"] for m in metas}

    added = 0
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
            added += 1
            session.add(Plugin(
                name=meta["name"],
                showname=meta["showname"],
                supported_types=",".join(meta["types"]),
                inputs_schema=inputs,
                version=version,
                is_enabled=True,
            ))

    removed: list[str] = []
    referenced: list[str] = []
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

    # 提示语面向运维：突出「有没有新装到东西」，而不是总数
    message = f"新增 {added} 个插件" if added else "未发现新插件"
    if removed:
        message += f"，清理失效插件 {len(removed)} 个"
    if referenced:
        message += f"；{len(referenced)} 个插件仍被通道引用未删除：{','.join(referenced)}"

    # 加载失败的插件目录（导入报错 / 没有可用子类）：一并在结果里返回，
    # 后台「刷新插件」据此提示，避免「插件明明拷进去了却不出现」只能翻日志
    errors = PluginRegistry.load_errors()

    return {
        "synced": len(metas),
        "added": added,
        "removed": removed,
        "referenced": referenced,
        "errors": errors,
        "message": message,
    }
