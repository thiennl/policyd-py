import asyncio
import logging
from typing import Dict

from policyd_py.actions.provider import ActionProvider
from policyd_py.config.settings import ScriptConfig
from policyd_py.script_runner import ScriptRunner

logger = logging.getLogger(__name__)


class ScriptActionProvider(ActionProvider):
    def __init__(self, config: ScriptConfig):
        self.config = config
        self.runner = ScriptRunner(timeout_seconds=config.timeout_seconds)

    async def lock_account(self, email: str, reason: str) -> None:
        await self._run(
            self.config.lock_command,
            {
                "action": "lock",
                "email": email,
                "reason": reason,
            },
        )

    async def unlock_account(self, email: str) -> None:
        await self._run(
            self.config.unlock_command,
            {
                "action": "unlock",
                "email": email,
            },
        )

    async def get_account_status(self, email: str) -> str:
        output = await self._run(
            self.config.status_command,
            {
                "action": "status",
                "email": email,
            },
        )
        return output.strip()

    def name(self) -> str:
        return "script"

    async def close(self) -> None:
        return

    async def _run(self, command: str, payload: Dict[str, object]) -> str:
        if not command.strip():
            raise RuntimeError("script command is not configured")
        return await self.runner.run(command, payload)
