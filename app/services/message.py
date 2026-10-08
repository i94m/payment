"""商户站内消息 + 邮件订阅通知。

站内消息（merchant_messages）始终落库；邮件按商户订阅开关叠加，
实际发信通道由支付设置 mail_channel/mail_config 决定（空=不发）。
"""
import json
import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, MerchantMessage

_log = logging.getLogger(__name__)


async def send_merchant_message(
    session: AsyncSession,
    merchant: Merchant,
    title: str,
    content: str,
    biz_type: str = "system",
    subscribe_flag: str = "",
) -> None:
    """写一条站内消息；subscribe_flag 非空且商户开启对应订阅且有邮箱时叠加邮件。

    任何失败只打日志，不影响主业务事务（调用方随外层事务一并提交消息行）。
    """
    session.add(MerchantMessage(
        merchant_id=merchant.id,
        title=title[:128],
        content=content[:500],
        biz_type=biz_type,
    ))
    if not subscribe_flag or not getattr(merchant, subscribe_flag, False):
        return
    if not merchant.email:
        return

    try:
        await send_mail(session, merchant.email, title, content)
    except Exception:
        _log.warning("订阅邮件发送失败 merchant_id=%s title=%s", merchant.id, title, exc_info=True)


async def send_mail(session: AsyncSession, to: str, subject: str, body: str) -> bool:
    """经 mail 插件发信；mail_channel 未配置时静默跳过。"""
    from app.services.plugin import PluginRegistry
    from app.services.settings import get_setting

    channel = await get_setting(session, "mail_channel", "")
    if not channel:
        return False
    raw_config = await get_setting(session, "mail_config", "")
    try:
        config = json.loads(raw_config) if isinstance(raw_config, str) else (raw_config or {})
    except (TypeError, ValueError):
        config = {}
    plugin = PluginRegistry.get_mail(str(channel), config or {})
    if not plugin:
        _log.warning("邮件插件未安装或未启用: %s", channel)
        return False
    return plugin.send(to, subject, f"<p>{body}</p>")
