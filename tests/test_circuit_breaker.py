"""Tests for circuit breaker and Redis connection pool configuration."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from policyd_py.config.settings import KeyDBConfig
from policyd_py.storage.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
)


class TestCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    async def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.failure_count, 0)

    async def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb._state = CircuitState.CLOSED
        cb._failure_count = 2
        self.assertEqual(cb.state, CircuitState.CLOSED)

    async def test_opens_after_failure_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb._state = CircuitState.CLOSED
        cb._failure_count = 2
        await cb._record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

    async def test_rejects_calls_when_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb._state = CircuitState.CLOSED
        await cb._record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        async def dummy():
            return "ok"

        with self.assertRaises(CircuitBreakerError):
            await cb.call(dummy)

    async def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=0,
        )
        cb._state = CircuitState.CLOSED
        await cb._record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        async def dummy():
            return "ok"

        result = await cb.call(dummy)
        self.assertEqual(result, "ok")
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    async def test_closes_after_success_threshold(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=0,
            success_threshold=2,
        )
        cb._state = CircuitState.CLOSED
        await cb._record_failure()

        async def dummy():
            return "ok"

        await cb.call(dummy)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        await cb.call(dummy)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    async def test_half_open_reopens_on_failure(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=0,
            success_threshold=2,
        )
        cb._state = CircuitState.CLOSED
        await cb._record_failure()

        async def failing():
            raise ValueError("test")

        with self.assertRaises(ValueError):
            await cb.call(failing)
        self.assertEqual(cb.state, CircuitState.OPEN)

    async def test_manual_reset(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb._state = CircuitState.CLOSED
        await cb._record_failure()
        await cb.reset()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.failure_count, 0)

    async def test_get_state_info(self):
        cb = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30,
            success_threshold=2,
        )
        info = cb.get_state_info()
        self.assertEqual(info["state"], "closed")
        self.assertEqual(info["failure_threshold"], 5)
        self.assertEqual(info["recovery_timeout"], 30)
        self.assertEqual(info["success_threshold"], 2)

    async def test_propagates_exception_from_call(self):
        cb = CircuitBreaker(failure_threshold=5)

        async def failing():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await cb.call(failing)

    async def test_time_until_recovery(self):
        import time
        cb = CircuitBreaker(recovery_timeout=10)
        cb._last_failure_time = time.time() - 3
        remaining = cb._time_until_recovery()
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 10)


class TestKeyDBConfigDefaults(unittest.TestCase):
    def test_default_max_connections(self):
        cfg = KeyDBConfig()
        self.assertEqual(cfg.max_connections, 50)

    def test_default_socket_keepalive(self):
        cfg = KeyDBConfig()
        self.assertTrue(cfg.socket_keepalive)

    def test_default_retry_on_timeout(self):
        cfg = KeyDBConfig()
        self.assertTrue(cfg.retry_on_timeout)

    def test_default_circuit_breaker_enable(self):
        cfg = KeyDBConfig()
        self.assertTrue(cfg.circuit_breaker_enable)

    def test_default_circuit_breaker_failure_threshold(self):
        cfg = KeyDBConfig()
        self.assertEqual(cfg.circuit_breaker_failure_threshold, 5)

    def test_default_circuit_breaker_recovery_timeout(self):
        cfg = KeyDBConfig()
        self.assertEqual(cfg.circuit_breaker_recovery_timeout, 30)

    def test_default_circuit_breaker_success_threshold(self):
        cfg = KeyDBConfig()
        self.assertEqual(cfg.circuit_breaker_success_threshold, 2)


class TestRedisClientCircuitBreakerIntegration(unittest.TestCase):
    def test_circuit_breaker_not_created_when_disabled(self):
        cfg = KeyDBConfig(circuit_breaker_enable=False)
        from policyd_py.storage.redis_client import RedisClient
        client = RedisClient(cfg)
        self.assertIsNone(client.circuit_breaker)

    def test_circuit_breaker_created_when_enabled(self):
        cfg = KeyDBConfig(
            circuit_breaker_enable=True,
            circuit_breaker_failure_threshold=3,
            circuit_breaker_recovery_timeout=15,
            circuit_breaker_success_threshold=1,
        )
        from policyd_py.storage.redis_client import RedisClient
        client = RedisClient(cfg)
        self.assertIsNone(client.circuit_breaker)

    def test_get_connection_pool_info_when_not_connected(self):
        cfg = KeyDBConfig()
        from policyd_py.storage.redis_client import RedisClient
        client = RedisClient(cfg)
        info = client.get_connection_pool_info()
        self.assertEqual(info["status"], "not_connected")

    def test_get_circuit_breaker_state_when_disabled(self):
        cfg = KeyDBConfig(circuit_breaker_enable=False)
        from policyd_py.storage.redis_client import RedisClient
        client = RedisClient(cfg)
        state = client.get_circuit_breaker_state()
        self.assertIsNone(state)


class TestCircuitBreakerStateTransitions(unittest.IsolatedAsyncioTestCase):
    async def test_closed_to_open_to_half_open_to_closed(self):
        cb = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=0,
            success_threshold=1,
        )

        async def success():
            return "ok"

        self.assertEqual(cb.state, CircuitState.CLOSED)

        await cb._record_failure()
        await cb._record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        await cb.call(success)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    async def test_closed_to_open_to_half_open_to_open(self):
        cb = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout=0,
            success_threshold=2,
        )

        async def fail():
            raise ValueError("fail")

        await cb._record_failure()
        await cb._record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

        with self.assertRaises(ValueError):
            await cb.call(fail)
        self.assertEqual(cb.state, CircuitState.OPEN)


if __name__ == "__main__":
    unittest.main()
