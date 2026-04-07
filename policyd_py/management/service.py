"""Management service that bridges the HTTP API to the PolicyHandler and ConfigManager."""

"""Management service that bridges the HTTP API to the PolicyHandler and ConfigManager."""

from typing import Any, Awaitable, Callable, Dict, Optional

from policyd_py.config.settings import AppConfig
from policyd_py.management.config_manager import ConfigManager
from policyd_py.policy.handler import PolicyHandler

ReloadCallback = Callable[[AppConfig], Awaitable[None]]


class ManagementService:
    """Exposes handler operations (health, stats, lock/unlock, config) to the HTTP API."""
    """Exposes handler operations (health, stats, lock/unlock, config) to the HTTP API."""
    def __init__(
        self,
        handler: PolicyHandler,
        config_manager: ConfigManager,
        reload_callback: Optional[ReloadCallback] = None,
    ):
        self.handler = handler
        self.config_manager = config_manager
        self.reload_callback = reload_callback

    async def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "features": {
                "adaptive_limits": self.handler.config.adaptive.enable,
                "progressive_penalty": self.handler.config.penalty.enable,
                "management_api": True,
            },
        }

    async def stats(self) -> Dict[str, Any]:
        payload = self.handler.get_stats_snapshot()
        if self.handler.action_manager:
            payload["action_metrics"] = self.handler.action_manager.get_metrics()
        payload["features"] = {
            "adaptive_limits": self.handler.config.adaptive.enable,
            "progressive_penalty": self.handler.config.penalty.enable,
        }
        return payload

    async def runtime_state(self, email: str) -> Dict[str, Any]:
        return await self.handler.get_runtime_state(email)

    async def reload_config(self) -> Dict[str, Any]:
        cfg = self.config_manager.reload()
        if self.reload_callback:
            await self.reload_callback(cfg)
        return {"status": "reloaded"}

    async def save_config(self, content: Optional[str], updates: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
        cfg = self.config_manager.save(content=content, updates=updates)
        if self.reload_callback:
            await self.reload_callback(cfg)
        return {"status": "saved"}

    async def lock_user(self, email: str, reason: str = "manual") -> Dict[str, Any]:
        await self.handler.lock_user_manually(email, reason)
        return {"status": "locked", "email": email}

    async def unlock_user(self, email: str) -> Dict[str, Any]:
        await self.handler.unlock_user_manually(email)
        return {"status": "unlocked", "email": email}

    async def reset_ratelimit(self, email: str) -> Dict[str, Any]:
        await self.handler.reset_ratelimit_manually(email)
        return {"status": "reset", "email": email}

    async def reset_penalty(self, email: str) -> Dict[str, Any]:
        await self.handler.reset_penalty_manually(email)
        return {"status": "reset", "email": email}
