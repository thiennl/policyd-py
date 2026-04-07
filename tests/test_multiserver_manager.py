import tempfile
import unittest

from policyd_py.multiserver.manager import Manager
from policyd_py.multiserver.models import RemoteAction, ServerInfo, ServerStatus


class FakeClient:
    def __init__(self):
        self.reset_penalty_calls = []
        self.lock_user_calls = []

    def reset_penalty(self, server, email: str) -> None:
        self.reset_penalty_calls.append((server.id, email))

    def lock_user(self, server, email: str, reason: str = "manual") -> None:
        self.lock_user_calls.append((server.id, email, reason))


class MultiserverManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregated_stats_include_new_feature_metrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Manager(f"{temp_dir}/registry.json")
            manager.registry.add_server(ServerInfo(id="s1", name="node-1", host="127.0.0.1", port=8080))
            manager.registry.add_server(ServerInfo(id="s2", name="node-2", host="127.0.0.2", port=8080))
            manager.status_cache = {
                "s1": ServerStatus(
                    server_id="s1",
                    server_name="node-1",
                    online=True,
                    stats={
                        "total_requests": 10,
                        "total_accepted": 8,
                        "total_rejected": 1,
                        "total_rate_limited": 1,
                        "total_blacklisted": 2,
                        "total_users_locked": 3,
                        "active_connections": 4,
                        "total_sliding_window_checks": 5,
                        "total_adaptive_adjustments": 6,
                        "total_adaptive_tightened": 2,
                        "total_adaptive_relaxed": 4,
                        "total_penalty_applied": 7,
                        "total_penalty_escalations": 3,
                    },
                ),
                "s2": ServerStatus(server_id="s2", server_name="node-2", online=False),
            }

            agg = manager.get_aggregated_stats()

            self.assertEqual(agg.total_servers, 2)
            self.assertEqual(agg.online_servers, 1)
            self.assertEqual(agg.offline_servers, 1)
            self.assertEqual(agg.total_requests, 10)
            self.assertEqual(agg.total_sliding_window_checks, 5)
            self.assertEqual(agg.total_adaptive_adjustments, 6)
            self.assertEqual(agg.total_adaptive_tightened, 2)
            self.assertEqual(agg.total_adaptive_relaxed, 4)
            self.assertEqual(agg.total_penalty_applied, 7)
            self.assertEqual(agg.total_penalty_escalations, 3)

    async def test_execute_reset_penalty_uses_client_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Manager(f"{temp_dir}/registry.json")
            manager.registry.add_server(ServerInfo(id="s1", name="node-1", host="127.0.0.1", port=8080))
            manager.client = FakeClient()

            result = await manager.execute_on_server(
                "s1",
                RemoteAction(server_id="s1", action="reset_penalty", params={"email": "user@example.com"}),
            )

            self.assertTrue(result.success)
            self.assertEqual(manager.client.reset_penalty_calls, [("s1", "user@example.com")])

    async def test_execute_lock_user_passes_reason(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = Manager(f"{temp_dir}/registry.json")
            manager.registry.add_server(ServerInfo(id="s1", name="node-1", host="127.0.0.1", port=8080))
            manager.client = FakeClient()

            result = await manager.execute_on_server(
                "s1",
                RemoteAction(server_id="s1", action="lock_user", params={"email": "user@example.com", "reason": "abuse"}),
            )

            self.assertTrue(result.success)
            self.assertEqual(manager.client.lock_user_calls, [("s1", "user@example.com", "abuse")])


if __name__ == "__main__":
    unittest.main()
