"""商户站内消息 + 邮件订阅通知。

站内消息（merchant_messages）始终落库；邮件按商户订阅开关叠加，
实际发信通道由支付设置 mail_channel/mail_config 决定（空=不发）。
"""
import asyncio
import json
import logging

from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, MerchantMessage

_log = logging.getLogger(__name__)

# 单次发信总超时（秒）：SMTP 卡死时也不把调用方事务拖成长事务
MAIL_TIMEOUT = 20


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
    """经 mail 插件发信；mail_channel 未配置时静默跳过。

    统一走插件的 `send_async`（原生异步或线程池包装），并加总超时兜底：
    历史上 SMTP 插件在事件循环里返回未 await 的 Future，导致「永远报成功」，
    且最坏阻塞事件循环 30s（密码重置验证码、结算打款都在链路上）。
    """
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
    try:
        ok = await asyncio.wait_for(
            plugin.send_async(to, subject, f"<p>{body}</p>"), timeout=MAIL_TIMEOUT,
        )
    except (TimeoutError, asyncio.TimeoutError):
        _log.warning("发信超时（%ss）: to=%s subject=%s", MAIL_TIMEOUT, to, subject)
        return False
    except Exception:
        _log.warning("发信异常: to=%s subject=%s", to, subject, exc_info=True)
        return False
    if not ok:
        _log.warning("发信失败: to=%s subject=%s channel=%s", to, subject, channel)
    return bool(ok)
