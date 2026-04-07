import unittest

from policyd_py.config.settings import AppConfig
from policyd_py.management.config_manager import ConfigManager
from policyd_py.management.service import ManagementService


class DummyActionManager:
    def get_metrics(self):
        return {"providers": {"webhook": {"lock_success": 3}}}


class DummyHandler:
    def __init__(self):
        self.config = AppConfig()
        self.config.adaptive.enable = True
        self.config.penalty.enable = True
        self.action_manager = DummyActionManager()
        self.lock_calls = []
        self.unlock_calls = []
        self.reset_calls = []
        self.reset_penalty_calls = []

    def get_stats_snapshot(self):
        return {
            "total_requests": 10,
            "total_sliding_window_checks": 4,
            "total_adaptive_adjustments": 3,
            "total_penalty_applied": 2,
        }

    async def get_runtime_state(self, email: str):
        return {"email": email, "locked": True, "penalty_count": 2}

    async def lock_user_manually(self, email: str, reason: str = "manual"):
        self.lock_calls.append((email, reason))

    async def unlock_user_manually(self, email: str):
        self.unlock_calls.append(email)

    async def reset_ratelimit_manually(self, email: str):
        self.reset_calls.append(email)

    async def reset_penalty_manually(self, email: str):
        self.reset_penalty_calls.append(email)


class DummyConfigManager(ConfigManager):
    def __init__(self):
        self._config = AppConfig()

    def reload(self):
        return self._config

    def save(self, content=None, updates=None):
        return self._config


class ManagementServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_and_stats_include_feature_flags_and_metrics(self):
        service = ManagementService(DummyHandler(), DummyConfigManager())

        health = await service.health()
        stats = await service.stats()

        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["features"]["adaptive_limits"])
        self.assertTrue(health["features"]["progressive_penalty"])
        self.assertEqual(stats["total_sliding_window_checks"], 4)
        self.assertEqual(stats["total_adaptive_adjustments"], 3)
        self.assertEqual(stats["total_penalty_applied"], 2)
        self.assertIn("action_metrics", stats)
        self.assertTrue(stats["features"]["adaptive_limits"])
        self.assertTrue(stats["features"]["progressive_penalty"])

    async def test_runtime_state_and_manual_actions_are_forwarded(self):
        handler = DummyHandler()
        service = ManagementService(handler, DummyConfigManager())

        state = await service.runtime_state("user@example.com")
        lock_result = await service.lock_user("user@example.com", "manual")
        unlock_result = await service.unlock_user("user@example.com")
        reset_result = await service.reset_ratelimit("user@example.com")
        reset_penalty_result = await service.reset_penalty("user@example.com")

        self.assertEqual(state["email"], "user@example.com")
        self.assertTrue(state["locked"])
        self.assertEqual(lock_result["status"], "locked")
        self.assertEqual(unlock_result["status"], "unlocked")
        self.assertEqual(reset_result["status"], "reset")
        self.assertEqual(reset_penalty_result["status"], "reset")
        self.assertEqual(handler.lock_calls, [("user@example.com", "manual")])
        self.assertEqual(handler.unlock_calls, ["user@example.com"])
        self.assertEqual(handler.reset_calls, ["user@example.com"])
        self.assertEqual(handler.reset_penalty_calls, ["user@example.com"])


if __name__ == "__main__":
    unittest.main()
