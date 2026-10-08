"""SMTP 邮件插件 — 使用 aiosmtplib 异步发送邮件"""
import asyncio
import logging
from email.mime.text import MIMEText

import aiosmtplib

from app.services.plugin_interfaces import BaseMail

logger = logging.getLogger(__name__)


class SmtpMail(BaseMail):
    """SMTP 邮件插件"""

    def __init__(self, config: dict):
        self.host = config.get("host", "")
        self.port = int(config.get("port", 465))
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.from_addr = config.get("from_addr", "")
        self.use_tls = config.get("use_tls", True)

    def send(self, to: str, subject: str, body: str) -> bool:
        """发送 HTML 邮件

        Args:
            to: 收件人地址
            subject: 邮件主题
            body: 邮件正文（HTML）

        Returns:
            True 发送成功, False 发送失败
        """
        if not to or not subject:
            logger.error("SmtpMail: recipient or subject is empty")
            return False

        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = to

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in an async context — schedule and wait
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return loop.run_in_executor(pool, self._send_sync, msg)
            else:
                loop.run_until_complete(self._send_async(msg))
        except RuntimeError:
            asyncio.run(self._send_async(msg))

        return True

    def _send_sync(self, msg) -> bool:
        """Fallback synchronous send"""
        try:
            asyncio.run(self._send_async(msg))
            return True
        except Exception as e:
            logger.error("SmtpMail sync fallback failed: %s", e)
            return False

    async def _send_async(self, msg) -> bool:
        """使用 aiosmtplib 异步发送"""
        try:
            await aiosmtplib.send(
                msg.as_string(),
                hostname=self.host,
                port=self.port,
                username=self.username or None,
                password=self.password or None,
                sender=self.from_addr,
                recipients=[msg["To"]],
                use_tls=self.use_tls,
                timeout=30,
            )
            logger.info("SmtpMail sent successfully: to=%s", msg["To"])
            return True
        except Exception as e:
            logger.error("SmtpMail send failed: %s", e)
            return False
