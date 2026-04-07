import os
import tempfile
import unittest

from policyd_py.config.settings import AppConfig, parse_rate_limits
from policyd_py.core.models import PolicyRequest
from policyd_py.policy.matcher import PolicyMatcher


class ConfigAndMatcherTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_rate_limits(self):
        limits = parse_rate_limits("100/1h:fixed_window,40/30m:1/45s,25/5m:sliding_window_counter")
        self.assertEqual(len(limits), 3)
        self.assertEqual(limits[0].algorithm, "fixed_window")
        self.assertEqual(limits[0].count, 100)
        self.assertEqual(limits[0].duration, 3600)
        self.assertEqual(limits[1].algorithm, "token_bucket")
        self.assertEqual(limits[1].count, 40)
        self.assertEqual(limits[1].duration, 1800)
        self.assertGreater(limits[1].refill_rate, 0)
        self.assertEqual(limits[2].algorithm, "sliding_window_counter")
        self.assertEqual(limits[2].count, 25)
        self.assertEqual(limits[2].duration, 300)

    def test_parse_external_action_webhook_and_web(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
[ExternalAction]
enable = true
provider = webhook
fallback_providers = script
continue_on_error = true
async_execution = true
async_queue_size = 200
async_workers = 4

[Webhook]
lock_url = https://example.com/lock
unlock_url = https://example.com/unlock
auth_type = bearer
auth_token = token-value
headers = X-App: policyd, X-Env: prod
retry_count = 5

[Script]
lock_command = /opt/policyd/bin/account_action.sh lock ${email} ${reason}
unlock_command = /opt/policyd/bin/account_action.sh unlock ${email}
status_command = /opt/policyd/bin/account_action.sh status ${email}

[Web]
enable = true
host = 0.0.0.0
port = 9090
username = admin
password = secret
bearer_token = tok123
cors_enabled = true
cors_origins = https://a.example,https://b.example

[Penalty]
enable = true
ttl = 2h
steps = 10m,1h

[AdaptiveLimits]
enable = true
authenticated_multiplier = 1.5
unauthenticated_multiplier = 0.5
local_sender_multiplier = 1.25
external_sender_multiplier = 0.75
trusted_multiplier = 2.0
trusted_account_lists = vip_accounts
trusted_domain_lists = local_domains
trusted_ip_lists = trusted_relays
minimum_multiplier = 0.5
maximum_multiplier = 3.0
"""
                )

            cfg = AppConfig.load(config_path)
            self.assertTrue(cfg.external_action.enable)
            self.assertEqual(cfg.external_action.provider, "webhook")
            self.assertEqual(cfg.external_action.fallback_providers, ["script"])
            self.assertTrue(cfg.external_action.continue_on_error)
            self.assertTrue(cfg.external_action.async_execution)
            self.assertEqual(cfg.external_action.async_queue_size, 200)
            self.assertEqual(cfg.external_action.async_workers, 4)
            self.assertEqual(cfg.webhook.lock_url, "https://example.com/lock")
            self.assertEqual(cfg.webhook.unlock_url, "https://example.com/unlock")
            self.assertEqual(cfg.webhook.auth_type, "bearer")
            self.assertEqual(cfg.webhook.auth_token, "token-value")
            self.assertEqual(cfg.webhook.headers.get("X-App"), "policyd")
            self.assertEqual(cfg.webhook.headers.get("X-Env"), "prod")
            self.assertEqual(cfg.webhook.retry_count, 5)

            self.assertTrue(cfg.web.enable)
            self.assertEqual(cfg.web.host, "0.0.0.0")
            self.assertEqual(cfg.web.port, 9090)
            self.assertEqual(cfg.web.username, "admin")
            self.assertEqual(cfg.web.password, "secret")
            self.assertEqual(cfg.web.bearer_token, "tok123")
            self.assertTrue(cfg.web.cors_enabled)
            self.assertEqual(cfg.web.cors_origins, ["https://a.example", "https://b.example"])

            self.assertTrue(cfg.penalty.enable)
            self.assertEqual(cfg.penalty.ttl, 7200)
            self.assertEqual(cfg.penalty.steps, [600, 3600])
            self.assertTrue(cfg.adaptive.enable)
            self.assertEqual(cfg.adaptive.authenticated_multiplier, 1.5)
            self.assertEqual(cfg.adaptive.unauthenticated_multiplier, 0.5)
            self.assertEqual(cfg.adaptive.local_sender_multiplier, 1.25)
            self.assertEqual(cfg.adaptive.external_sender_multiplier, 0.75)
            self.assertEqual(cfg.adaptive.trusted_multiplier, 2.0)
            self.assertEqual(cfg.adaptive.trusted_account_lists, ["vip_accounts"])
            self.assertEqual(cfg.adaptive.trusted_domain_lists, ["local_domains"])
            self.assertEqual(cfg.adaptive.trusted_ip_lists, ["trusted_relays"])
            self.assertEqual(cfg.adaptive.minimum_multiplier, 0.5)
            self.assertEqual(cfg.adaptive.maximum_multiplier, 3.0)

    def test_parse_script_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
[ExternalAction]
enable = true
provider = script

[Script]
lock_command = /opt/policyd/bin/account_action.sh lock ${email} ${reason}
unlock_command = /opt/policyd/bin/account_action.sh unlock ${email}
status_command = /opt/policyd/bin/account_action.sh status ${email}
notify_command = /opt/policyd/bin/notify_action.sh ${event} ${email}
timeout_seconds = 15
"""
                )

            cfg = AppConfig.load(config_path)
            self.assertTrue(cfg.external_action.enable)
            self.assertEqual(cfg.external_action.provider, "script")
            self.assertEqual(cfg.script.lock_command, "/opt/policyd/bin/account_action.sh lock ${email} ${reason}")
            self.assertEqual(cfg.script.unlock_command, "/opt/policyd/bin/account_action.sh unlock ${email}")
            self.assertEqual(cfg.script.status_command, "/opt/policyd/bin/account_action.sh status ${email}")
            self.assertEqual(cfg.script.notify_command, "/opt/policyd/bin/notify_action.sh ${event} ${email}")
            self.assertEqual(cfg.script.timeout_seconds, 15)

    async def test_policy_matcher(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_domains_file = os.path.join(temp_dir, "local_domains.txt")
            with open(local_domains_file, "w", encoding="utf-8") as f:
                f.write("example.com\nlocal.com\n")

            config_path = os.path.join(temp_dir, "config.ini")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    """
[Limits]
enable_policyd = true
policy_check_state = RCPT
default_quota = 10/1h:fixed_window

[DomainLists]
local_domains = file://{domains}

[Quotas]
internal_quota = 100/1h:fixed_window
external_quota = 20/1h:fixed_window

[Policies]
internal = @local_domains:@local_domains:internal_quota
external = @local_domains:*:external_quota
""".format(domains=local_domains_file)
                )

            cfg = AppConfig.load(config_path)
            matcher = PolicyMatcher(cfg, None)

            p1 = await matcher.match(
                PolicyRequest(
                    sender="alice@example.com",
                    recipient="bob@local.com",
                    sasl_username="alice@example.com",
                )
            )
            self.assertIsNotNone(p1)
            self.assertEqual(p1.name, "internal")

            p2 = await matcher.match(
                PolicyRequest(sender="alice@example.com", recipient="x@gmail.com", sasl_username="")
            )
            self.assertIsNotNone(p2)
            self.assertEqual(p2.name, "external")


if __name__ == "__main__":
    unittest.main()
