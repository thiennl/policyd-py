import asyncio
import contextlib
import time
from typing import Dict, List

from policyd_py.multiserver.client import Client
from policyd_py.multiserver.models import AggregatedStats, RemoteAction, RemoteActionResult, ServerStatus
from policyd_py.multiserver.registry import Registry


class Manager:
    def __init__(self, registry_path: str, health_check_interval_seconds: int = 30):
        self.registry = Registry(registry_path, auto_save=True)
        self.client = Client(timeout_seconds=10)
        self.status_cache: Dict[str, ServerStatus] = {}
        self.health_check_interval_seconds = health_check_interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_health_checks())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def get_server_status(self, server_id: str) -> ServerStatus:
        return self.status_cache.get(server_id, ServerStatus(server_id=server_id, online=False))

    def get_all_server_statuses(self) -> List[ServerStatus]:
        return list(self.status_cache.values())

    async def refresh_server_status(self, server_id: str) -> ServerStatus:
        server = self.registry.get_server(server_id)
        status = await asyncio.to_thread(self._check_server, server)
        self.status_cache[server_id] = status
        return status

    async def refresh_all_server_statuses(self) -> None:
        servers = self.registry.get_enabled_servers()
        results = await asyncio.gather(*[asyncio.to_thread(self._check_server, server) for server in servers], return_exceptions=True)
        for item in results:
            if isinstance(item, Exception):
                continue
            self.status_cache[item.server_id] = item

    def get_aggregated_stats(self) -> AggregatedStats:
        agg = AggregatedStats()
        agg.total_servers = self.registry.count()

        for status in self.status_cache.values():
            if status.online:
                agg.online_servers += 1
                if status.stats:
                    agg.total_requests += int(status.stats.get("total_requests", 0))
                    agg.total_accepted += int(status.stats.get("total_accepted", 0))
                    agg.total_rejected += int(status.stats.get("total_rejected", 0))
                    agg.total_rate_limited += int(status.stats.get("total_rate_limited", 0))
                    agg.total_blacklisted += int(status.stats.get("total_blacklisted", 0))
                    agg.total_users_locked += int(status.stats.get("total_users_locked", 0))
                    agg.active_connections += int(status.stats.get("active_connections", 0))
                    agg.total_sliding_window_checks += int(status.stats.get("total_sliding_window_checks", 0))
                    agg.total_adaptive_adjustments += int(status.stats.get("total_adaptive_adjustments", 0))
                    agg.total_adaptive_tightened += int(status.stats.get("total_adaptive_tightened", 0))
                    agg.total_adaptive_relaxed += int(status.stats.get("total_adaptive_relaxed", 0))
                    agg.total_penalty_applied += int(status.stats.get("total_penalty_applied", 0))
                    agg.total_penalty_escalations += int(status.stats.get("total_penalty_escalations", 0))
                    agg.server_stats[status.server_id] = status.stats
            else:
                agg.offline_servers += 1

        return agg

    async def execute_on_server(self, server_id: str, action: RemoteAction) -> RemoteActionResult:
        server = self.registry.get_server(server_id)
        result = RemoteActionResult(server_id=server_id, success=False, timestamp=time.time())

        try:
            await asyncio.to_thread(self._execute_action_sync, server, action)
            result.success = True
            result.message = "Action completed successfully"
        except Exception as exc:
            result.error = str(exc)
        return result

    async def execute_on_all_servers(self, action: RemoteAction) -> List[RemoteActionResult]:
        servers = self.registry.get_enabled_servers()
        tasks = [
            self.execute_on_server(
                server.id,
                RemoteAction(server_id=server.id, action=action.action, params=action.params),
            )
            for server in servers
        ]
        return await asyncio.gather(*tasks)

    async def execute_on_servers(self, server_ids: List[str], action: RemoteAction) -> List[RemoteActionResult]:
        tasks = [
            self.execute_on_server(
                server_id,
                RemoteAction(server_id=server_id, action=action.action, params=action.params),
            )
            for server_id in server_ids
        ]
        return await asyncio.gather(*tasks)

    async def _run_health_checks(self) -> None:
        await self.refresh_all_server_statuses()
        while True:
            await asyncio.sleep(max(self.health_check_interval_seconds, 1))
            with contextlib.suppress(Exception):
                await self.refresh_all_server_statuses()

    def _check_server(self, server):
        status = self.client.health_check(server)
        if status.online:
            try:
                status.stats = self.client.get_stats(server)
            except Exception as exc:
                status.error = str(exc)
        return status

    def _execute_action_sync(self, server, action: RemoteAction) -> None:
        if action.action == "reload_config":
            self.client.reload_config(server)
        elif action.action == "save_config":
            self.client.save_config(server)
        elif action.action == "lock_user":
            self.client.lock_user(server, action.params.get("email", ""), str(action.params.get("reason", "manual")))
        elif action.action == "unlock_user":
            self.client.unlock_user(server, action.params.get("email", ""))
        elif action.action == "reset_ratelimit":
            self.client.reset_ratelimit(server, action.params.get("email", ""))
        elif action.action == "reset_penalty":
            self.client.reset_penalty(server, action.params.get("email", ""))
        else:
            method = action.params.get("method", "POST")
            endpoint = action.params.get("endpoint", "")
            payload = action.params.get("payload")
            if not endpoint:
                raise ValueError("endpoint is required for generic action")
            self.client.execute_action(server, method, endpoint, payload)
