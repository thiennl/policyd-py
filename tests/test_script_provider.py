import unittest
from unittest.mock import AsyncMock

from policyd_py.actions.script_provider import ScriptActionProvider
from policyd_py.config.settings import ScriptConfig
from policyd_py.notification.notifier import Notifier


class ScriptIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_script_action_provider_uses_configured_templates(self):
        provider = ScriptActionProvider(
            ScriptConfig(
                lock_command="/bin/echo lock ${email} ${reason}",
                unlock_command="/bin/echo unlock ${email}",
                status_command="/bin/echo active",
                timeout_seconds=5,
            )
        )
        provider.runner.run = AsyncMock(return_value="active")

        await provider.lock_account("user@example.com", "manual")
        await provider.unlock_account("user@example.com")
        status = await provider.get_account_status("user@example.com")

        self.assertEqual(status, "active")
        self.assertEqual(provider.runner.run.await_count, 3)
        first_call = provider.runner.run.await_args_list[0]
        self.assertIn("${email}", first_call.args[0])
        self.assertEqual(first_call.args[1]["action"], "lock")
        self.assertEqual(first_call.args[1]["email"], "user@example.com")
        self.assertEqual(first_call.args[1]["reason"], "manual")

    async def test_notifier_calls_script_notification_when_configured(self):
        from policyd_py.config.settings import AppConfig

        cfg = AppConfig()
        cfg.script.notify_command = "/bin/echo notify ${event} ${email}"
        notifier = Notifier(cfg)
        notifier._script_runner.run = AsyncMock(return_value="")

        await notifier.notify_rate_limit("user@example.com", "Rate limit exceeded")
        await notifier.notify_user_locked("user@example.com", 600)
        await notifier.notify_user_unlocked("user@example.com")

        events = [call.args[1]["event"] for call in notifier._script_runner.run.await_args_list]
        self.assertEqual(events, ["rate_limit", "locked", "unlocked"])


if __name__ == "__main__":
    unittest.main()
