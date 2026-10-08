"""管理员告警通道：WARNING 日志 + 后台通知中心（admin_messages）落库。

供队列监控 cron（queue_monitor_task）等系统级巡检调用；
落库失败只打日志并回滚，绝不影响调用方主流程。
"""
import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import AdminMessage

_log = logging.getLogger(__name__)


async def send_admin_alert(session: AsyncSession, title: str, content: str) -> None:
    """发一条管理员告警：日志必达，并写入后台通知中心（未读状态全局）。"""
    _log.warning("系统告警 [%s] %s", title, content)
    try:
        session.add(AdminMessage(title=title[:128], content=content[:500], biz_type="alert"))
        await session.commit()
    except Exception:
        await session.rollback()
        _log.warning("告警写入通知中心失败 title=%s", title, exc_info=True)
