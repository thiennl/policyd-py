"""Async Redis client wrapper with convenience methods for policyd operations."""

"""Async Redis client wrapper with convenience methods for policyd operations."""

import logging
from typing import Any, List, Optional

from policyd_py.config.settings import KeyDBConfig

logger = logging.getLogger(__name__)


class RedisClient:
    """Thin async wrapper around redis.asyncio for policyd key operations."""
    """Thin async wrapper around redis.asyncio for policyd key operations."""
    def __init__(self, config: KeyDBConfig):
        self.config = config
        self.pool: Optional[object] = None
        self.client: Optional[object] = None

    async def connect(self) -> None:
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RuntimeError("redis package is required for RedisClient") from exc

        if not self.config.hosts:
            raise ValueError("Redis hosts list is empty")

        host_port = self.config.hosts[0].split(":")
        host = host_port[0]
        port = int(host_port[1]) if len(host_port) > 1 else 6379

        self.pool = redis.ConnectionPool(
            host=host,
            port=port,
            db=self.config.db,
            password=self.config.password or None,
            socket_timeout=self.config.read_timeout,
            socket_connect_timeout=self.config.connect_timeout,
            decode_responses=True,
        )
        self.client = redis.Redis.from_pool(self.pool)
        await self.client.ping()
        logger.info("Connected to Redis at %s:%s", host, port)

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
            logger.info("Redis connection closed")

    async def script_load(self, script: str) -> str:
        return await self.client.script_load(script)

    async def evalsha(self, sha: str, keys: List[str], args: List[Any]) -> Any:
        return await self.client.evalsha(sha, len(keys), *keys, *args)

    async def eval(self, script: str, keys: List[str], args: List[Any]) -> Any:
        return await self.client.eval(script, len(keys), *keys, *args)

    async def hgetall(self, key: str) -> dict:
        return await self.client.hgetall(key)

    async def exists(self, key: str) -> int:
        return await self.client.exists(key)

    async def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        return bool(await self.client.set(key, value, ex=max(ttl_seconds, 1)))

    async def setnx(self, key: str, value: str, ttl_seconds: int) -> bool:
        return bool(await self.client.set(key, value, ex=max(ttl_seconds, 1), nx=True))

    async def delete(self, key: str) -> int:
        return await self.client.delete(key)

    async def get(self, key: str) -> Optional[str]:
        return await self.client.get(key)

    async def incr(self, key: str) -> int:
        return await self.client.incr(key)

    async def sadd(self, key: str, values: List[str]) -> int:
        if not values:
            return 0
        return await self.client.sadd(key, *values)

    async def sismember(self, key: str, value: str) -> bool:
        return bool(await self.client.sismember(key, value))

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self.client.expire(key, ttl_seconds))

    async def ttl(self, key: str) -> int:
        return await self.client.ttl(key)

    async def lpush(self, key: str, value: str) -> int:
        return await self.client.lpush(key, value)

    async def brpop(self, key: str, timeout: int = 1) -> Optional[tuple[str, str]]:
        return await self.client.brpop(key, timeout=timeout)

    async def zadd(self, key: str, mapping: dict) -> int:
        return await self.client.zadd(key, mapping)

    async def zrangebyscore(self, key: str, min: float | str, max: float | str) -> list[str]:
        return await self.client.zrangebyscore(key, min, max)

    async def zrem(self, key: str, values: list[str]) -> int:
        if not values:
            return 0
        return await self.client.zrem(key, *values)

    async def pubsub(self):
        return self.client.pubsub()

    async def keys(self, pattern: str) -> List[str]:
        return await self.client.keys(pattern)

    async def enable_keyspace_notifications(self) -> None:
        try:
            await self.client.config_set("notify-keyspace-events", "Ex")
            logger.info("Enabled Redis keyspace notifications (Ex)")
        except Exception as exc:
            logger.warning("Failed to enable keyspace notifications: %s", exc)
