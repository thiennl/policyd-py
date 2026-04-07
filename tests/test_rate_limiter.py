import time
import unittest
from unittest.mock import patch

from policyd_py.config.settings import AppConfig, RateLimit
from policyd_py.ratelimit.limiter import RateLimiter


class DummyRedis:
    def __init__(self):
        self.hashes = {}
        self.deleted_keys = []

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def delete(self, key):
        self.deleted_keys.append(key)
        return 1


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_limit_evaluates_all_limits(self):
        limiter = RateLimiter(DummyRedis(), AppConfig())
        calls = []

        async def fake_get_usage(sender, limit):
            return 1 if limit.count == 5 else 2

        async def fake_check_single_limit(sender, limit):
            calls.append(limit.count)
            return limit.count != 1, (1 if limit.count == 5 else 2)

        limiter.get_usage = fake_get_usage
        limiter._check_single_limit = fake_check_single_limit

        limits = [
            RateLimit(count=5, duration=60, algorithm="fixed_window"),
            RateLimit(count=1, duration=3600, algorithm="fixed_window"),
        ]
        allowed, message, err = await limiter.check_limit("user@example.com", limits, "rcpt@example.com")

        self.assertFalse(allowed)
        self.assertIsNone(err)
        self.assertEqual(calls, [5, 1])
        self.assertIn("Rate limit exceeded: 1 messages per 3600s", message)

    async def test_get_usage_sliding_window_counter_interpolates_previous_bucket(self):
        redis_client = DummyRedis()
        limiter = RateLimiter(redis_client, AppConfig())
        limit = RateLimit(count=20, duration=60, algorithm="sliding_window_counter")
        redis_client.hashes["ratelimit:sliding:user@example.com:60"] = {
            "previous_count": "10",
            "current_count": "2",
            "window_start": str(int(time.time()) - 30),
        }

        with patch("policyd_py.ratelimit.limiter.time.time", return_value=float(redis_client.hashes["ratelimit:sliding:user@example.com:60"]["window_start"]) + 30.0):
            usage = await limiter.get_usage("user@example.com", limit)

        self.assertEqual(usage, 7)

    async def test_reset_limit_uses_sliding_window_key(self):
        redis_client = DummyRedis()
        limiter = RateLimiter(redis_client, AppConfig())
        limit = RateLimit(count=20, duration=60, algorithm="sliding_window_counter")

        await limiter.reset_limit("user@example.com", limit)

        self.assertEqual(redis_client.deleted_keys, ["ratelimit:sliding:user@example.com:60"])


if __name__ == "__main__":
    unittest.main()
