import unittest
from unittest import IsolatedAsyncioTestCase

from policyd_py.actions.manager import ActionManager, ManagerConfig
from policyd_py.actions.provider import ActionProvider


class _FakeProvider(ActionProvider):
    def __init__(self, name: str, lock_fail: bool = False, unlock_fail: bool = False):
        self._name = name
        self._lock_fail = lock_fail
        self._unlock_fail = unlock_fail
        self.lock_calls: list[tuple[str, str]] = []
        self.unlock_calls: list[str] = []

    async def lock_account(self, email: str, reason: str) -> None:
        if self._lock_fail:
            raise RuntimeError(f"{self._name} lock failed")
        self.lock_calls.append((email, reason))

    async def unlock_account(self, email: str) -> None:
        if self._unlock_fail:
            raise RuntimeError(f"{self._name} unlock failed")
        self.unlock_calls.append(email)

    async def get_account_status(self, email: str) -> str:
        return "active"

    def name(self) -> str:
        return self._name

    async def close(self) -> None:
        pass


class ActionManagerSyncTests(IsolatedAsyncioTestCase):
    async def test_lock_calls_primary(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=False))
        await mgr.lock_account("user@test.com", "test reason")
        self.assertEqual(primary.lock_calls, [("user@test.com", "test reason")])

    async def test_unlock_calls_primary(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=False))
        await mgr.unlock_account("user@test.com")
        self.assertEqual(primary.unlock_calls, ["user@test.com"])

    async def test_fallback_used_when_primary_fails(self):
        primary = _FakeProvider("primary", lock_fail=True)
        fallback = _FakeProvider("fallback")
        mgr = ActionManager(primary=primary, fallbacks=[fallback], config=ManagerConfig(async_execution=False))
        await mgr.lock_account("user@test.com", "failover")
        self.assertEqual(fallback.lock_calls, [("user@test.com", "failover")])

    async def test_all_providers_fail_raises(self):
        primary = _FakeProvider("primary", lock_fail=True)
        fallback = _FakeProvider("fallback", lock_fail=True)
        mgr = ActionManager(primary=primary, fallbacks=[fallback], config=ManagerConfig(async_execution=False))
        with self.assertRaises(RuntimeError) as ctx:
            await mgr.lock_account("user@test.com", "all fail")
        self.assertIn("all providers failed", str(ctx.exception))

    async def test_get_account_status(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=False))
        status = await mgr.get_account_status("user@test.com")
        self.assertEqual(status, "active")

    async def test_metrics_recorded(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=False))
        await mgr.lock_account("user@test.com", "metric test")
        metrics = mgr.get_metrics()
        self.assertIn("primary", metrics["providers"])
        self.assertEqual(metrics["providers"]["primary"]["total_calls"], 1)
        self.assertEqual(metrics["providers"]["primary"]["success_calls"], 1)

    async def test_close_shuts_down_providers(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=False))
        await mgr.close()


class ActionManagerAsyncTests(IsolatedAsyncioTestCase):
    async def test_async_lock_queues_work(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=True, queue_size=10, workers=1))
        await mgr.start()
        try:
            await mgr.lock_account("user@test.com", "async reason")
            await mgr._queue.join()
            self.assertEqual(primary.lock_calls, [("user@test.com", "async reason")])
        finally:
            await mgr.close()

    async def test_async_queue_full_raises(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=True, queue_size=1, workers=1))
        await mgr.start()
        try:
            await mgr._queue.put(("lock", "block@test.com", "block"))
            with self.assertRaises(RuntimeError) as ctx:
                await mgr.lock_account("user@test.com", "full")
            self.assertIn("full", str(ctx.exception))
        finally:
            await mgr.close()

    async def test_async_unlock_queues_work(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=True, queue_size=10, workers=1))
        await mgr.start()
        try:
            await mgr.unlock_account("user@test.com")
            await mgr._queue.join()
            self.assertEqual(primary.unlock_calls, ["user@test.com"])
        finally:
            await mgr.close()

    async def test_queue_metrics(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=True, queue_size=100, workers=1))
        await mgr.start()
        try:
            metrics = mgr.get_metrics()
            self.assertEqual(metrics["queue_capacity"], 100)
        finally:
            await mgr.close()

    async def test_closing_rejects_new_actions(self):
        primary = _FakeProvider("primary")
        mgr = ActionManager(primary=primary, config=ManagerConfig(async_execution=True, queue_size=10, workers=1))
        await mgr.start()
        await mgr.close()
        with self.assertRaises(RuntimeError) as ctx:
            await mgr.lock_account("user@test.com", "closed")
        self.assertIn("closing", str(ctx.exception))
