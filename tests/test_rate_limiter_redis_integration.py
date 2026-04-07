import asyncio
import contextlib
import importlib.util
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from policyd_py.config.settings import AppConfig, RateLimit
from policyd_py.ratelimit.limiter import RateLimiter
from policyd_py.storage.redis_client import RedisClient

HAS_REDIS_PY = importlib.util.find_spec("redis") is not None
REDIS_SERVER_BIN = shutil.which("redis-server") or shutil.which("valkey-server")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(HAS_REDIS_PY and REDIS_SERVER_BIN, "redis-server/valkey-server and redis package are required")
class RedisLuaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.port = _pick_free_port()
        self.process = subprocess.Popen(
            [
                REDIS_SERVER_BIN,
                "--save",
                "",
                "--appendonly",
                "no",
                "--port",
                str(self.port),
                "--bind",
                "127.0.0.1",
                "--dir",
                self.temp_dir.name,
                "--notify-keyspace-events",
                "Ex",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.config = AppConfig()
        self.config.keydb.hosts = [f"127.0.0.1:{self.port}"]
        self.config.keydb.db = 0
        self.config.limits.ratelimit_use_lua = True

        self.redis_client = RedisClient(self.config.keydb)
        await self._wait_for_redis()
        await self.redis_client.connect()
        self.limiter = RateLimiter(self.redis_client, self.config)
        await self.limiter.init_scripts()

    async def asyncTearDown(self):
        with contextlib.suppress(Exception):
            await self.redis_client.close()
        if getattr(self, "process", None) is not None:
            self.process.terminate()
            with contextlib.suppress(Exception):
                self.process.wait(timeout=5)
        if getattr(self, "temp_dir", None) is not None:
            self.temp_dir.cleanup()

    async def _wait_for_redis(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
                writer.close()
                await writer.wait_closed()
                return
            except Exception:
                await asyncio.sleep(0.05)
        raise RuntimeError("redis server did not start in time")

    async def test_sliding_window_and_token_bucket_lua_paths(self):
        sliding = RateLimit(count=2, duration=60, algorithm="sliding_window_counter")
        token_bucket = RateLimit(count=2, duration=60, refill_rate=0.0, algorithm="token_bucket")

        allowed_1, _, err_1 = await self.limiter.check_limit("user@example.com", [sliding], "rcpt@example.com")
        allowed_2, _, err_2 = await self.limiter.check_limit("user@example.com", [sliding], "rcpt@example.com")
        allowed_3, message_3, err_3 = await self.limiter.check_limit("user@example.com", [sliding], "rcpt@example.com")

        self.assertTrue(allowed_1)
        self.assertTrue(allowed_2)
        self.assertFalse(allowed_3)
        self.assertIsNone(err_1)
        self.assertIsNone(err_2)
        self.assertIsNone(err_3)
        self.assertIn("Rate limit exceeded", message_3)
        self.assertGreaterEqual(await self.limiter.get_usage("user@example.com", sliding), 2)

        await self.limiter.reset_limit("user@example.com", sliding)
        self.assertEqual(await self.limiter.get_usage("user@example.com", sliding), 0)

        tb_allowed_1, _, _ = await self.limiter.check_limit("bucket@example.com", [token_bucket], "rcpt@example.com")
        tb_allowed_2, _, _ = await self.limiter.check_limit("bucket@example.com", [token_bucket], "rcpt@example.com")
        tb_allowed_3, tb_message_3, _ = await self.limiter.check_limit("bucket@example.com", [token_bucket], "rcpt@example.com")

        self.assertTrue(tb_allowed_1)
        self.assertTrue(tb_allowed_2)
        self.assertFalse(tb_allowed_3)
        self.assertIn("Rate limit exceeded", tb_message_3)


if __name__ == "__main__":
    unittest.main()
