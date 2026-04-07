import asyncio
import unittest

from policyd_py.config.settings import AppConfig, PolicyRule, RateLimit
from policyd_py.core.models import PolicyRequest
from policyd_py.policy.handler import PolicyHandler
from policyd_py.policy.matcher import PolicyMatcher
from policyd_py.validation.validator import EmailValidator


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.sets = {}
        self.zsets = {}
        self.lists = {}

    async def exists(self, key):
        return 1 if key in self.kv else 0

    async def set(self, key, value, ttl_seconds):
        self.kv[key] = (value, ttl_seconds)
        return True

    async def setnx(self, key, value, ttl_seconds):
        if key in self.kv:
            return False
        self.kv[key] = (value, ttl_seconds)
        return True

    async def delete(self, key):
        existed = key in self.kv or key in self.sets
        self.kv.pop(key, None)
        self.sets.pop(key, None)
        return 1 if existed else 0

    async def get(self, key):
        value = self.kv.get(key)
        if value is None:
            return None
        return value[0]

    async def incr(self, key):
        value, ttl = self.kv.get(key, (0, None))
        new_value = int(value) + 1
        self.kv[key] = (new_value, ttl)
        return new_value

    async def expire(self, key, ttl_seconds):
        if key not in self.kv:
            return False
        value, _ = self.kv[key]
        self.kv[key] = (value, ttl_seconds)
        return True

    async def ttl(self, key):
        if key not in self.kv:
            return -2
        _, ttl_seconds = self.kv[key]
        return -1 if ttl_seconds is None else ttl_seconds

    async def sadd(self, key, values):
        s = self.sets.setdefault(key, set())
        s.update(values)
        return len(values)

    async def sismember(self, key, value):
        return value in self.sets.get(key, set())

    async def pubsub(self):
        return FakePubSub()

    async def zadd(self, key, mapping):
        zset = self.zsets.setdefault(key, {})
        zset.update(mapping)
        return len(mapping)

    async def zrangebyscore(self, key, min_score, max_score):
        zset = self.zsets.get(key, {})
        lower = float("-inf") if min_score == "-inf" else float(min_score)
        upper = float(max_score)
        return [member for member, score in zset.items() if lower <= float(score) <= upper]

    async def zrem(self, key, members):
        zset = self.zsets.get(key, {})
        removed = 0
        for member in members:
            if member in zset:
                removed += 1
                del zset[member]
        return removed

    async def lpush(self, key, value):
        bucket = self.lists.setdefault(key, [])
        bucket.insert(0, value)
        return len(bucket)

    async def brpop(self, key, timeout=1):
        bucket = self.lists.setdefault(key, [])
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            if bucket:
                return key, bucket.pop()
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.01)


class FakePubSub:
    async def subscribe(self, channel):
        return None

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        await asyncio.sleep(0)
        return None

    async def unsubscribe(self, channel):
        return None

    async def close(self):
        return None


class FakeRateLimiter:
    def __init__(self, allow=False, usage=0):
        self.allow = allow
        self.usage = usage
        self.reset_calls = []
        self.last_checked_limits = []

    async def check_limit(self, sender, limits, recipient):
        self.last_checked_limits = [limit.model_copy() for limit in limits]
        if self.allow:
            return True, "usage=1/100", None
        return False, "Rate limit exceeded: 100 messages per 3600s (current: 100)", None

    async def get_usage(self, sender, limit):
        return self.usage

    async def reset_limit(self, sender, limit):
        self.reset_calls.append((sender, limit.algorithm, limit.duration, limit.count))


class FakeNotifier:
    def __init__(self):
        self.rate_limit_messages = []
        self.locked = []
        self.unlocked = []

    async def notify_rate_limit(self, email, message):
        self.rate_limit_messages.append((email, message))

    async def notify_user_locked(self, email, duration_seconds):
        self.locked.append((email, duration_seconds))

    async def notify_user_unlocked(self, email):
        self.unlocked.append(email)


class FakeLDAP:
    async def get_domains(self, ldap_uri):
        return ["example.com", "internal.example"]


class FakeActionManager:
    def __init__(self):
        self.locked = []
        self.unlocked = []
        self.closed = False

    async def lock_account(self, email, reason):
        self.locked.append((email, reason))

    async def unlock_account(self, email):
        self.unlocked.append(email)

    async def close(self):
        self.closed = True

    def get_metrics(self):
        return {}


class HandlerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def build_config(self):
        cfg = AppConfig()
        cfg.limits.enable_policyd = True
        cfg.limits.policy_check_state = "RCPT"
        cfg.limits.default_quota = [RateLimit(count=100, duration=3600, algorithm="fixed_window")]
        cfg.limits.quotas = {"external_quota": [RateLimit(count=100, duration=3600, algorithm="fixed_window")]}
        cfg.limits.domain_lists = {"local_domains": ["example.com"]}
        cfg.limits.policies = [
            PolicyRule(name="local_external", sender="@local_domains", recipient="*", quota="external_quota")
        ]
        return cfg

    async def test_rate_limit_triggers_lock_and_notify(self):
        cfg = self.build_config()
        redis_client = FakeRedis()
        action_manager = FakeActionManager()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=False, usage=100)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
            action_manager=action_manager,
        )

        await handler.start()
        try:
            req = PolicyRequest(
                sender="user@example.com",
                recipient="receiver@gmail.com",
                protocol_state="RCPT",
                sasl_username="user@example.com",
            )
            resp = await handler.handle(req)
            await asyncio.sleep(0.05)

            self.assertEqual(resp.action, "DEFER")
            self.assertIn(("user@example.com", "Rate limit exceeded: 100 messages per 3600s (current: 100)"), action_manager.locked)
            self.assertIn("lock:user@example.com", redis_client.kv)
            self.assertTrue(notifier.rate_limit_messages)
        finally:
            await handler.stop()

    async def test_unlock_event_when_no_longer_exceeding(self):
        cfg = self.build_config()
        redis_client = FakeRedis()
        action_manager = FakeActionManager()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        email = "user@example.com"

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
            action_manager=action_manager,
        )

        await handler._process_unlock_event(email)

        self.assertIn(email, action_manager.unlocked)
        self.assertIn(email, notifier.unlocked)

    async def test_unlock_recheck_uses_stored_selector_and_limits_metadata(self):
        cfg = self.build_config()
        cfg.limits.default_quota = [RateLimit(count=999, duration=3600, algorithm="fixed_window")]
        policy_limit = RateLimit(count=100, duration=3600, algorithm="fixed_window")

        redis_client = FakeRedis()
        redis_client.kv["lock:user@example.com"] = ("rate limit", 600)
        redis_client.kv["lockmeta:user@example.com"] = (
            '{"selector":"user@example.com","limits":[{"count":100,"duration":3600,"refill_rate":0.0,"algorithm":"fixed_window","unlimited":false}]}',
            600,
        )
        action_manager = FakeActionManager()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=100)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
            action_manager=action_manager,
        )

        still_exceeding = await handler._is_still_exceeding_limit(
            "user@example.com",
            selector="user@example.com",
            limits=[policy_limit],
        )
        self.assertTrue(still_exceeding)

        await handler._process_unlock_event("user@example.com")

        self.assertEqual(action_manager.unlocked, [])
        self.assertEqual(redis_client.kv["lock:user@example.com"][0], "Extended: still exceeding limit")
        self.assertIn("lockmeta:user@example.com", redis_client.kv)

    async def test_manual_unlock_clears_redis_lock_and_forces_unlock(self):
        cfg = self.build_config()
        redis_client = FakeRedis()
        redis_client.kv["lock:user@example.com"] = ("rate limit", 600)
        action_manager = FakeActionManager()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=False, usage=100)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
            action_manager=action_manager,
        )

        await handler.unlock_user_manually("user@example.com")

        self.assertNotIn("lock:user@example.com", redis_client.kv)
        self.assertIn("user@example.com", action_manager.unlocked)
        self.assertIn("user@example.com", notifier.unlocked)

    async def test_reset_penalty_uses_selector_from_lock_metadata(self):
        cfg = self.build_config()
        redis_client = FakeRedis()
        redis_client.kv["penalty:alias@example.com"] = (2, 3600)
        redis_client.kv["lockmeta:user@example.com"] = ('{"selector":"alias@example.com","limits":[]}', 600)
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
        )

        await handler.reset_penalty_manually("user@example.com")

        self.assertNotIn("penalty:alias@example.com", redis_client.kv)

    async def test_refresh_runtime_dependencies_restarts_unlock_listener(self):
        cfg = self.build_config()
        redis_client = FakeRedis()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
        )

        await handler.start()
        self.assertIsNone(handler._unlock_listener_task)

        action_manager = FakeActionManager()
        await handler.refresh_runtime_dependencies(
            config=cfg,
            ldap_client=None,
            action_manager=action_manager,
        )

        self.assertIsNotNone(handler._unlock_listener_task)
        self.assertFalse(handler._unlock_listener_task.done())

        await handler.stop()
        self.assertTrue(action_manager.closed)
        self.assertIsNone(handler._unlock_listener_task)

    async def test_ldap_list_loader_populates_redis_and_local_domains(self):
        cfg = self.build_config()
        cfg.limits.domain_lists = {"local_domains": ["__LDAP__:ldap://ldap.example.com/dc=example,dc=com"]}

        redis_client = FakeRedis()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
            ldap_client=FakeLDAP(),
        )

        await handler._load_lists_to_redis()

        members = redis_client.sets.get("policyd:list:local_domains", set())
        self.assertIn("example.com", members)
        self.assertIn("internal.example", members)
        self.assertTrue(handler._is_local_domain("example.com"))

    async def test_progressive_penalty_escalates_lock_duration(self):
        cfg = self.build_config()
        cfg.penalty.enable = True
        cfg.penalty.ttl = 3600
        cfg.penalty.steps = [60, 600, 3600]

        redis_client = FakeRedis()
        action_manager = FakeActionManager()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=False, usage=100)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
            action_manager=action_manager,
        )
        await handler.start()
        try:
            req = PolicyRequest(
                sender="user@example.com",
                recipient="receiver@gmail.com",
                protocol_state="RCPT",
                sasl_username="user@example.com",
            )

            await handler.handle(req)
            await asyncio.sleep(0.05)
            self.assertEqual(notifier.locked[-1], ("user@example.com", 60))
            self.assertEqual(redis_client.kv["penalty:user@example.com"], (1, 3600))
            self.assertEqual(handler.get_stats_snapshot()["total_penalty_applied"], 1)

            await redis_client.delete("lock:user@example.com")
            await handler.handle(req)
            await asyncio.sleep(0.05)
            self.assertEqual(notifier.locked[-1], ("user@example.com", 600))
            self.assertEqual(redis_client.kv["penalty:user@example.com"], (2, 3600))
            self.assertEqual(handler.get_stats_snapshot()["total_penalty_applied"], 2)
            self.assertEqual(handler.get_stats_snapshot()["total_penalty_escalations"], 1)
        finally:
            await handler.stop()

    async def test_adaptive_limit_scales_quota_for_trusted_authenticated_sender(self):
        cfg = self.build_config()
        cfg.adaptive.enable = True
        cfg.adaptive.authenticated_multiplier = 1.5
        cfg.adaptive.local_sender_multiplier = 1.25
        cfg.adaptive.trusted_multiplier = 2.0
        cfg.adaptive.trusted_account_lists = ["vip_accounts"]
        cfg.limits.account_lists = {"vip_accounts": ["ceo@example.com"]}

        redis_client = FakeRedis()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
        )
        await handler._load_lists_to_redis()

        req = PolicyRequest(
            sender="ceo@example.com",
            recipient="receiver@gmail.com",
            protocol_state="RCPT",
            sasl_username="ceo@example.com",
        )
        resp = await handler.handle(req)

        self.assertEqual(resp.action, "DUNNO")
        self.assertEqual(len(limiter.last_checked_limits), 1)
        self.assertEqual(limiter.last_checked_limits[0].count, 200)
        stats = handler.get_stats_snapshot()
        self.assertEqual(stats["total_adaptive_adjustments"], 1)
        self.assertEqual(stats["total_adaptive_relaxed"], 1)

    async def test_adaptive_limit_tightens_quota_for_untrusted_external_sender(self):
        cfg = self.build_config()
        cfg.adaptive.enable = True
        cfg.adaptive.unauthenticated_multiplier = 0.5
        cfg.adaptive.external_sender_multiplier = 0.75
        cfg.adaptive.minimum_multiplier = 0.25
        cfg.adaptive.maximum_multiplier = 2.0

        redis_client = FakeRedis()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
        )
        await handler._load_lists_to_redis()

        req = PolicyRequest(
            sender="attacker@evil.example",
            recipient="receiver@gmail.com",
            protocol_state="RCPT",
            client_address="203.0.113.10",
        )
        resp = await handler.handle(req)

        self.assertEqual(resp.action, "DUNNO")
        self.assertEqual(len(limiter.last_checked_limits), 1)
        self.assertEqual(limiter.last_checked_limits[0].count, 50)
        stats = handler.get_stats_snapshot()
        self.assertEqual(stats["total_adaptive_adjustments"], 1)
        self.assertEqual(stats["total_adaptive_tightened"], 1)

    async def test_runtime_state_reports_lock_penalty_and_usage(self):
        cfg = self.build_config()
        cfg.penalty.enable = True
        cfg.penalty.ttl = 3600
        cfg.penalty.steps = [60, 600]
        cfg.limits.default_quota = [RateLimit(count=10, duration=60, algorithm="sliding_window_counter")]

        redis_client = FakeRedis()
        redis_client.kv["lock:user@example.com"] = ("rate limit", 600)
        redis_client.kv["penalty:user@example.com"] = (2, 3600)
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=7)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
        )
        await handler._load_lists_to_redis()

        state = await handler.get_runtime_state("user@example.com")

        self.assertTrue(state["locked"])
        self.assertEqual(state["lock_reason"], "rate limit")
        self.assertEqual(state["lock_ttl_seconds"], 600)
        self.assertEqual(state["penalty_count"], 2)
        self.assertEqual(state["penalty_ttl_seconds"], 3600)
        self.assertTrue(any(item["algorithm"] == "sliding_window_counter" for item in state["usage"]))
        sliding_usage = next(item for item in state["usage"] if item["algorithm"] == "sliding_window_counter")
        self.assertEqual(sliding_usage["usage"], 7)

    async def test_sliding_window_requests_are_counted_in_stats(self):
        cfg = self.build_config()
        cfg.limits.default_quota = [RateLimit(count=20, duration=300, algorithm="sliding_window_counter")]
        cfg.limits.quotas = {}
        cfg.limits.policies = []

        redis_client = FakeRedis()
        notifier = FakeNotifier()
        matcher = PolicyMatcher(cfg, redis_client)
        validator = EmailValidator(cfg.validation)
        limiter = FakeRateLimiter(allow=True, usage=0)

        handler = PolicyHandler(
            config=cfg,
            redis_client=redis_client,
            ratelimit_engine=limiter,
            validator=validator,
            matcher=matcher,
            notifier=notifier,
        )
        await handler._load_lists_to_redis()

        req = PolicyRequest(
            sender="user@example.com",
            recipient="receiver@gmail.com",
            protocol_state="RCPT",
            sasl_username="user@example.com",
        )
        resp = await handler.handle(req)

        self.assertEqual(resp.action, "DUNNO")
        self.assertEqual(handler.get_stats_snapshot()["total_sliding_window_checks"], 1)


if __name__ == "__main__":
    unittest.main()
