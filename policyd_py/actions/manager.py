"""Manages pluggable lock/unlock action providers with fallback and async queue support."""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import List, Optional

from policyd_py.actions.provider import ActionProvider

logger = logging.getLogger(__name__)


@dataclass
class ManagerConfig:
    """Runtime configuration for the ActionManager."""
    continue_on_error: bool = False
    async_execution: bool = False
    queue_size: int = 1000
    workers: int = 10


class ActionManager:
    """Coordinates primary and fallback action providers for lock/unlock operations.

    Supports synchronous execution or an async worker queue for non-blocking dispatch.
    """
    """Coordinates primary and fallback action providers for lock/unlock operations.

    Supports synchronous execution or an async worker queue for non-blocking dispatch.
    """
    def __init__(
        self,
        primary: ActionProvider,
        fallbacks: Optional[List[ActionProvider]] = None,
        config: Optional[ManagerConfig] = None,
    ):
        self.primary = primary
        self.fallbacks = fallbacks or []
        self.config = config or ManagerConfig()
        self._queue: Optional[asyncio.Queue] = None
        self._workers: List[asyncio.Task] = []
        self._closing = False
        self._metrics_lock = Lock()
        self._metrics = {
            "providers": {},
            "queue_size": 0,
            "queue_capacity": max(self.config.queue_size, 0),
        }

    async def start(self) -> None:
        if self.config.async_execution:
            self._queue = asyncio.Queue(maxsize=max(self.config.queue_size, 1))
            for idx in range(max(self.config.workers, 1)):
                self._workers.append(asyncio.create_task(self._worker(idx)))

    async def close(self) -> None:
        self._closing = True
        if self._queue is not None:
            await self._queue.join()
            for _ in self._workers:
                await self._queue.put(None)
        for task in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        providers = [self.primary, *self.fallbacks]
        for provider in providers:
            try:
                await provider.close()
            except Exception as exc:
                logger.warning("Failed to close provider %s: %s", provider.name(), exc)

    async def lock_account(self, email: str, reason: str) -> None:
        if self.config.async_execution:
            if self._queue is None:
                raise RuntimeError("ActionManager not started")
            if self._closing:
                raise RuntimeError("ActionManager is closing")
            try:
                self._queue.put_nowait(("lock", email, reason))
                self._update_queue_metrics()
            except asyncio.QueueFull as exc:
                raise RuntimeError("action queue is full") from exc
            return
        await self._execute_sync("lock", email, reason)

    async def unlock_account(self, email: str) -> None:
        if self.config.async_execution:
            if self._queue is None:
                raise RuntimeError("ActionManager not started")
            if self._closing:
                raise RuntimeError("ActionManager is closing")
            try:
                self._queue.put_nowait(("unlock", email, ""))
                self._update_queue_metrics()
            except asyncio.QueueFull as exc:
                raise RuntimeError("action queue is full") from exc
            return
        await self._execute_sync("unlock", email, "")

    async def get_account_status(self, email: str) -> str:
        start = time.monotonic()
        try:
            status = await self.primary.get_account_status(email)
            self._record_metric(self.primary.name(), True, time.monotonic() - start)
            return status
        except Exception:
            self._record_metric(self.primary.name(), False, time.monotonic() - start)
            raise

    def get_metrics(self) -> dict:
        with self._metrics_lock:
            snapshot = {
                "providers": {},
                "queue_size": self._metrics["queue_size"],
                "queue_capacity": self._metrics["queue_capacity"],
            }
            for provider, metric in self._metrics["providers"].items():
                total_calls = metric["total_calls"]
                avg_duration_ms = 0.0
                if total_calls > 0:
                    avg_duration_ms = (metric["total_duration_seconds"] * 1000.0) / float(total_calls)
                snapshot["providers"][provider] = {
                    "total_calls": total_calls,
                    "success_calls": metric["success_calls"],
                    "failed_calls": metric["failed_calls"],
                    "total_duration_ms": int(metric["total_duration_seconds"] * 1000.0),
                    "avg_duration_ms": avg_duration_ms,
                }
            return snapshot

    async def _worker(self, idx: int) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if item is None:
                self._update_queue_metrics()
                self._queue.task_done()
                return
            action, email, reason = item
            self._update_queue_metrics()
            try:
                await self._execute_sync(action, email, reason)
            except Exception as exc:
                logger.error("Async action failed action=%s email=%s worker=%s error=%s", action, email, idx, exc)
            finally:
                self._queue.task_done()

    async def _execute_sync(self, action: str, email: str, reason: str) -> None:
        providers = [self.primary, *self.fallbacks]
        last_error = None

        for idx, provider in enumerate(providers):
            start = time.monotonic()
            try:
                if action == "lock":
                    await provider.lock_account(email, reason)
                elif action == "unlock":
                    await provider.unlock_account(email)
                else:
                    raise RuntimeError(f"unknown action {action}")
                self._record_metric(provider.name(), True, time.monotonic() - start)
                return
            except Exception as exc:
                self._record_metric(provider.name(), False, time.monotonic() - start)
                last_error = exc
                logger.warning("Provider failed provider=%s action=%s email=%s error=%s", provider.name(), action, email, exc)
                if idx > 0 and not self.config.continue_on_error:
                    break
                if idx == 0 and not self.config.continue_on_error and self.fallbacks:
                    continue

        raise RuntimeError(f"all providers failed for {action} on {email}: {last_error}")

    def _update_queue_metrics(self) -> None:
        if self._queue is None:
            return
        with self._metrics_lock:
            self._metrics["queue_size"] = self._queue.qsize()

    def _record_metric(self, provider: str, success: bool, duration_seconds: float) -> None:
        with self._metrics_lock:
            entry = self._metrics["providers"].setdefault(
                provider,
                {
                    "total_calls": 0,
                    "success_calls": 0,
                    "failed_calls": 0,
                    "total_duration_seconds": 0.0,
                },
            )
            entry["total_calls"] += 1
            entry["total_duration_seconds"] += max(duration_seconds, 0.0)
            if success:
                entry["success_calls"] += 1
            else:
                entry["failed_calls"] += 1
