"""SMTP 邮件插件 — 使用 aiosmtplib 异步发送邮件。

两条入口，语义必须一致（都返回真实发送结果）：
- `send_async`（业务链路的唯一入口）：原生异步，await aiosmtplib，不占线程、
  不阻塞事件循环；
- `send`（同步兼容入口，仅限无事件循环的调用方）：asyncio.run 执行同一实现。

历史 bug（P0-5）：旧实现在事件循环里 `loop.run_in_executor(...)` 后直接 return
未 await 的 Future —— 恒真值导致 `send_mail` 无条件报成功，且 ThreadPoolExecutor
的 with 块退出即同步等待，最坏把事件循环阻塞 30s。
"""
import asyncio
import logging
from email.mime.text import MIMEText

import aiosmtplib

from app.services.plugin_interfaces import BaseMail

logger = logging.getLogger(__name__)

# 单次发信超时（秒）：避免上游 SMTP 卡死时把调用方事务拖成长事务
SMTP_TIMEOUT = 15


class SmtpMail(BaseMail):
    """SMTP 邮件插件"""

    def __init__(self, config: dict):
        self.host = config.get("host", "")
        self.port = int(config.get("port", 465))
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.from_addr = config.get("from_addr", "")
        self.use_tls = config.get("use_tls", True)

    def _build_message(self, to: str, subject: str, body: str):
        msg = MIMEText(body, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.from_addr
        msg["To"] = to
        return msg

    def send(self, to: str, subject: str, body: str) -> bool:
        """同步发送（无事件循环的调用方）。

        在事件循环内被调用会抛 RuntimeError —— 异步链路必须走 `send_async`，
        否则「在循环里同步等待」本身就是死锁。
        """
        if not to or not subject:
            logger.error("SmtpMail: recipient or subject is empty")
            return False
        msg = self._build_message(to, subject, body)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._send_async(msg))
        raise RuntimeError("SmtpMail.send 不能在事件循环内调用，请使用 send_async")

    async def send_async(self, to: str, subject: str, body: str) -> bool:
        """异步发送（业务链路入口，返回真实结果）。"""
        if not to or not subject:
            logger.error("SmtpMail: recipient or subject is empty")
            return False
        return await self._send_async(self._build_message(to, subject, body))

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
                timeout=SMTP_TIMEOUT,
            )
            logger.info("SmtpMail sent successfully: to=%s", msg["To"])
            return True
        except Exception as e:
            logger.error("SmtpMail send failed: %s", e)
            return False
