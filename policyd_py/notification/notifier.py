"""Multi-channel notification dispatcher (Telegram, Discord, Email, Script)."""

"""Multi-channel notification dispatcher (Telegram, Discord, Email, Script)."""

import asyncio
import json
import logging
import smtplib
import time
from email.message import EmailMessage
from typing import Optional, TYPE_CHECKING

from policyd_py.config.settings import AppConfig
from policyd_py.script_runner import ScriptRunner

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)


class Notifier:
    """Dispatches rate-limit and lock/unlock alerts across configured channels."""
    """Dispatches rate-limit and lock/unlock alerts across configured channels."""
    def __init__(self, config: AppConfig):
        self.config = config
        self._session: Optional[object] = None
        self._script_runner = ScriptRunner(timeout_seconds=config.script.timeout_seconds)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def notify_rate_limit(self, email: str, message: str) -> None:
        tasks = [asyncio.create_task(self._dispatch_builtin_notifications(message))]
        if self.config.script.notify_command:
            tasks.append(asyncio.create_task(self._send_script_notification("rate_limit", email, message)))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch_builtin_notifications(self, message: str) -> None:
        tasks = []
        if self.config.telegram.enable:
            tasks.append(asyncio.create_task(self._send_telegram(message)))
        if self.config.discord.enable:
            tasks.append(asyncio.create_task(self._send_discord(message)))
        if self.config.email.enable:
            tasks.append(asyncio.create_task(self._send_email(message)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def notify_user_locked(self, email: str, duration_seconds: int) -> None:
        msg = f"User account locked: {email} for {duration_seconds}s due to rate limit violation"
        tasks = [asyncio.create_task(self._dispatch_builtin_notifications(msg))]
        if self.config.script.notify_command:
            tasks.append(asyncio.create_task(self._send_script_notification("locked", email, msg, duration_seconds)))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def notify_user_unlocked(self, email: str) -> None:
        message = f"User account unlocked: {email}"
        tasks = [asyncio.create_task(self._dispatch_builtin_notifications(message))]
        if self.config.script.notify_command:
            tasks.append(asyncio.create_task(self._send_script_notification("unlocked", email, message)))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _get_session(self):
        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError("aiohttp is required for Telegram/Discord notifications") from exc

        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def _send_telegram(self, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.config.telegram.bot_token}/sendMessage"
        payload = {"chat_id": self.config.telegram.chat_id, "text": message}

        session = await self._get_session()
        try:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.warning("Telegram notification failed: %s %s", response.status, body)
        except Exception as exc:
            logger.warning("Telegram notification error: %s", exc)

    async def _send_discord(self, message: str) -> None:
        template = self.config.discord.message_template or "{message}"
        content = template.replace("{message}", message).replace("{timestamp}", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        url = f"https://discord.com/api/channels/{self.config.discord.channel_id}/messages"
        payload = {"content": content}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bot {self.config.discord.bot_token}",
        }

        session = await self._get_session()
        try:
            async with session.post(url, data=json.dumps(payload), headers=headers) as response:
                if response.status not in (200, 201):
                    body = await response.text()
                    logger.warning("Discord notification failed: %s %s", response.status, body)
        except Exception as exc:
            logger.warning("Discord notification error: %s", exc)

    async def _send_email(self, message: str) -> None:
        await asyncio.to_thread(self._send_email_sync, message)

    async def _send_script_notification(
        self,
        event: str,
        email: str,
        message: str,
        duration_seconds: int = 0,
    ) -> None:
        try:
            await self._script_runner.run(
                self.config.script.notify_command,
                {
                    "action": "notify",
                    "event": event,
                    "email": email,
                    "message": message,
                    "duration_seconds": duration_seconds,
                },
            )
        except Exception as exc:
            logger.warning("Script notification error event=%s email=%s: %s", event, email, exc)

    def _send_email_sync(self, message: str) -> None:
        if not self.config.email.smtp_host or not self.config.email.from_addr or not self.config.email.to:
            logger.warning("Email notification skipped: incomplete email config")
            return

        body = (self.config.email.template or "{message}").replace("{message}", message)

        from_value = self.config.email.from_addr
        if self.config.email.from_display_name:
            from_value = f"{self.config.email.from_display_name} <{self.config.email.from_addr}>"

        msg = EmailMessage()
        msg["From"] = from_value
        msg["To"] = ", ".join(self.config.email.to)
        msg["Subject"] = self.config.email.subject
        msg.set_content(body)

        with smtplib.SMTP(self.config.email.smtp_host, self.config.email.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if self.config.email.smtp_user:
                smtp.login(self.config.email.smtp_user, self.config.email.smtp_password)
            smtp.send_message(msg)
