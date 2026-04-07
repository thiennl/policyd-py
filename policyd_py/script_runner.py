"""Safely executes shell command templates with variable substitution."""

"""Safely executes shell command templates with variable substitution."""

import asyncio
import logging
import shlex
import time
from string import Template
from typing import Dict

logger = logging.getLogger(__name__)


class ScriptRunner:
    """Renders a command template with payload variables and executes it as a subprocess."""
    """Renders a command template with payload variables and executes it as a subprocess."""
    def __init__(self, timeout_seconds: int = 10):
        self.timeout_seconds = max(timeout_seconds, 1)

    async def run(self, command_template: str, payload: Dict[str, object]) -> str:
        command = self._render(command_template, payload)
        argv = shlex.split(command)
        if not argv:
            raise RuntimeError("script command rendered to empty argv")

        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError(f"script timed out after {self.timeout_seconds}s: {argv[0]}") from exc

        stdout_text = stdout.decode("utf-8", errors="ignore").strip()
        stderr_text = stderr.decode("utf-8", errors="ignore").strip()

        if process.returncode != 0:
            message = stderr_text or stdout_text or f"exit code {process.returncode}"
            raise RuntimeError(f"script failed: {message}")

        if stderr_text:
            logger.debug("Script stderr from %s: %s", argv[0], stderr_text)
        return stdout_text

    def _render(self, command_template: str, payload: Dict[str, object]) -> str:
        data = {key: self._to_string(value) for key, value in payload.items()}
        data.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return Template(command_template).safe_substitute(data)

    @staticmethod
    def _to_string(value: object) -> str:
        if value is None:
            return ""
        return str(value)
