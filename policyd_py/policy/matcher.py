"""Policy rule matching against sender/recipient patterns and named lists."""

"""Policy rule matching against sender/recipient patterns and named lists."""

import logging
from typing import Optional, TYPE_CHECKING

from policyd_py.config.settings import AppConfig, PolicyRule
from policyd_py.core.models import PolicyRequest

if TYPE_CHECKING:
    from policyd_py.storage.redis_client import RedisClient

logger = logging.getLogger(__name__)


class PolicyMatcher:
    """Matches a ``PolicyRequest`` against configured policy rules.

    Supports wildcards (``*``), SASL-based patterns (``@sasl``), domain
    matching (``@list_name``), and same-domain routing (``@same_domain``).
    """
    """Matches a ``PolicyRequest`` against configured policy rules.

    Supports wildcards (``*``), SASL-based patterns (``@sasl``), domain
    matching (``@list_name``), and same-domain routing (``@same_domain``).
    """
    def __init__(self, config: AppConfig, redis_client: Optional["RedisClient"] = None):
        self.config = config
        self.redis = redis_client

    async def match(self, request: PolicyRequest) -> Optional[PolicyRule]:
        sender = request.sender or ""
        recipient = request.recipient or ""
        sasl_user = request.sasl_username or ""

        for policy in self.config.limits.policies:
            sender_match = await self._match_sender(sender, sasl_user, policy.sender)
            if not sender_match:
                continue

            recipient_match = await self._match_recipient(sender, recipient, policy.recipient)
            if not recipient_match:
                continue

            return policy

        return None

    async def _match_sender(self, sender: str, sasl_user: str, pattern: str) -> bool:
        if pattern == "*":
            return True

        if pattern.startswith("@sasl"):
            if not sasl_user:
                return False

            if pattern == "@sasl":
                return True

            parts = pattern.split(":", 1)
            if len(parts) == 2:
                return await self._match_email_pattern(sasl_user, parts[1])
            return False

        return await self._match_email_pattern(sender, pattern)

    async def _match_recipient(self, sender: str, recipient: str, pattern: str) -> bool:
        if pattern == "*":
            return True

        if pattern == "@same_domain":
            return _get_domain(sender) == _get_domain(recipient)

        return await self._match_email_pattern(recipient, pattern)

    async def _match_email_pattern(self, email: str, pattern: str) -> bool:
        if not email:
            return False

        if pattern.startswith("@"):
            list_name = pattern[1:]
            return await self._is_in_list(email, list_name)

        return email.lower() == pattern.lower()

    async def _is_in_list(self, email: str, list_name: str) -> bool:
        email = email.lower()
        domain = _get_domain(email)
        key = f"policyd:list:{list_name}"

        if self.redis:
            try:
                if domain and await self.redis.sismember(key, domain):
                    return True
                if await self.redis.sismember(key, email):
                    return True
            except Exception as exc:
                logger.warning("Redis list lookup failed for %s: %s", list_name, exc)

        domain_list = self.config.limits.domain_lists.get(list_name, [])
        if domain and domain in [x.lower() for x in domain_list]:
            return True

        account_list = self.config.limits.account_lists.get(list_name, [])
        if email in [x.lower() for x in account_list]:
            return True

        return False



def _get_domain(email: str) -> str:
    parts = email.split("@")
    if len(parts) == 2:
        return parts[1].lower()
    return ""
