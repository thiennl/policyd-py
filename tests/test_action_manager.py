import unittest

from policyd_py.actions.manager import ActionManager, ManagerConfig


class FakeProvider:
    def __init__(self, provider_name: str, should_fail: bool = False):
        self._name = provider_name
        self.should_fail = should_fail
        self.locked = []
        self.unlocked = []
        self.closed = False

    async def lock_account(self, email: str, reason: str) -> None:
        if self.should_fail:
            raise RuntimeError("provider lock failed")
        self.locked.append((email, reason))

    async def unlock_account(self, email: str) -> None:
        if self.should_fail:
            raise RuntimeError("provider unlock failed")
        self.unlocked.append(email)

    async def get_account_status(self, email: str) -> str:
        if self.should_fail:
            raise RuntimeError("provider status failed")
        return "active"

    def name(self) -> str:
        return self._name

    async def close(self) -> None:
        self.closed = True


class ActionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_provider_is_used_when_primary_fails(self):
        primary = FakeProvider("primary", should_fail=True)
        fallback = FakeProvider("fallback", should_fail=False)

        manager = ActionManager(primary=primary, fallbacks=[fallback], config=ManagerConfig())
        await manager.lock_account("user@example.com", "rate limited")

        self.assertEqual(primary.locked, [])
        self.assertEqual(fallback.locked, [("user@example.com", "rate limited")])

        metrics = manager.get_metrics()
        self.assertIn("primary", metrics["providers"])
        self.assertIn("fallback", metrics["providers"])
        self.assertEqual(metrics["providers"]["primary"]["failed_calls"], 1)
        self.assertEqual(metrics["providers"]["fallback"]["success_calls"], 1)

        await manager.close()
        self.assertTrue(primary.closed)
        self.assertTrue(fallback.closed)


if __name__ == "__main__":
    unittest.main()
