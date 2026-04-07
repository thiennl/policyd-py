"""Email syntax validation, DNS deliverability check, and blacklist enforcement."""

"""Email syntax validation, DNS deliverability check, and blacklist enforcement."""

import asyncio
import logging
import re
import socket
from typing import Optional, Set, Tuple

from policyd_py.config.settings import ValidationConfig

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")


class EmailValidator:
    """Validates email addresses and checks against configurable blacklists."""
    """Validates email addresses and checks against configurable blacklists."""
    def __init__(self, config: ValidationConfig):
        self.config = config
        self.sender_blacklist: Set[str] = set()
        self.recipient_blacklist: Set[str] = set()
        self.domain_blacklist: Set[str] = set()
        self._reload_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self.reload_blacklists()
        if self.config.enable_blacklist and self.config.blacklist_auto_reload:
            self._reload_task = asyncio.create_task(self._auto_reload_loop())

    async def stop(self) -> None:
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass

    async def _auto_reload_loop(self) -> None:
        while True:
            await asyncio.sleep(max(self.config.blacklist_reload_interval, 10))
            await self.reload_blacklists()

    async def reload_blacklists(self) -> None:
        self.sender_blacklist = _load_file_to_set(self.config.sender_blacklist_file)
        self.recipient_blacklist = _load_file_to_set(self.config.recipient_blacklist_file)
        self.domain_blacklist = _load_file_to_set(self.config.domain_blacklist_file)

    def validate_sender_syntax(self, sender: str) -> Tuple[bool, str]:
        if not self.config.validate_sender_syntax:
            return True, ""
        if not sender or not EMAIL_RE.match(sender):
            return False, "Invalid sender email syntax"
        return True, ""

    def validate_recipient_syntax(self, recipient: str) -> Tuple[bool, str]:
        if not self.config.validate_recipient_syntax and not self.config.validate_recipient:
            return True, ""
        if not recipient or not EMAIL_RE.match(recipient):
            return False, "Invalid recipient email syntax"
        return True, ""

    async def validate_recipient_deliverability(self, recipient: str) -> Tuple[bool, str]:
        if not self.config.validate_recipient_deliverability:
            return True, ""

        domain = _extract_domain(recipient)
        if not domain:
            return False, "Missing recipient domain"

        try:
            loop = asyncio.get_running_loop()
            await loop.getaddrinfo(domain, None)
            return True, ""
        except (socket.gaierror, OSError):
            return False, f"Recipient domain not resolvable: {domain}"

    def check_sender_blacklist(self, sender: str) -> Tuple[bool, str]:
        if not self.config.enable_blacklist:
            return False, ""
        if sender.lower() in self.sender_blacklist:
            return True, f"Sender blacklisted: {sender}"
        return False, ""

    def check_recipient_blacklist(self, recipient: str) -> Tuple[bool, str]:
        if not self.config.enable_blacklist:
            return False, ""
        if recipient.lower() in self.recipient_blacklist:
            return True, f"Recipient blacklisted: {recipient}"
        return False, ""

    def check_domain_blacklist(self, domain: str) -> Tuple[bool, str]:
        if not self.config.enable_blacklist:
            return False, ""
        if domain.lower() in self.domain_blacklist:
            return True, f"Domain blacklisted: {domain}"
        return False, ""


def _extract_domain(email: str) -> str:
    parts = (email or "").split("@")
    if len(parts) == 2:
        return parts[1].lower()
    return ""


def _load_file_to_set(path: str) -> Set[str]:
    if not path:
        return set()

    try:
        with open(path, "r", encoding="utf-8") as handle:
            values = set()
            for line in handle:
                line = line.strip().lower()
                if not line or line.startswith("#"):
                    continue
                values.add(line)
            return values
    except FileNotFoundError:
        logger.warning("Blacklist file not found: %s", path)
        return set()
    except OSError as exc:
        logger.warning("Failed to load blacklist file %s: %s", path, exc)
        return set()
