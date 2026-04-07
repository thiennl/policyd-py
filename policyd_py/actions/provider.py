"""Protocol and data models for pluggable action providers (lock/unlock)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol


class ActionType(str, enum.Enum):
    """Enumeration of supported action types."""
    LOCK = "lock"
    UNLOCK = "unlock"
    GET_STATUS = "get_status"


class ActionProvider(Protocol):
    """Protocol that all action providers must implement."""
    """Protocol that all action providers must implement."""
    async def lock_account(self, email: str, reason: str) -> None:
        ...

    async def unlock_account(self, email: str) -> None:
        ...

    async def get_account_status(self, email: str) -> str:
        ...

    def name(self) -> str:
        ...

    async def close(self) -> None:
        ...


@dataclass
class ActionContext:
    action: ActionType
    email: str
    reason: str = ""
    client_ip: str = ""
    sasl_username: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionResult:
    success: bool
    provider: str
    error: Optional[str] = None
    response_data: Dict[str, Any] = field(default_factory=dict)
