"""Rate limiting engine with Lua-backed Redis algorithms.

Supports three algorithms: ``token_bucket``, ``fixed_window``, and
``sliding_window_counter``. Each Lua script returns ``{allowed, usage}``
where ``allowed`` is 0 or 1 and ``usage`` is the current count.
"""

"""Rate limiting engine with Lua-backed Redis algorithms.

Supports three algorithms: ``token_bucket``, ``fixed_window``, and
``sliding_window_counter``. Each Lua script returns ``{allowed, usage}``
where ``allowed`` is 0 or 1 and ``usage`` is the current count.
"""

import logging
import math
import time
from typing import List, Optional, Tuple

from policyd_py.config.settings import AppConfig, RateLimit
from policyd_py.storage.redis_client import RedisClient

logger = logging.getLogger(__name__)

TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local time_res = redis.call('TIME')
local now_ms = tonumber(time_res[1]) * 1000 + math.floor(tonumber(time_res[2]) / 1000)

local tokens = tonumber(redis.call('HGET', key, 'tokens'))
local last_refill_ms = tonumber(redis.call('HGET', key, 'last_refill_ms'))

if tokens == nil then
    tokens = capacity
    last_refill_ms = now_ms
else
    local elapsed_ms = now_ms - last_refill_ms
    local elapsed_sec = elapsed_ms / 1000.0
    local refill_amount = elapsed_sec * refill_rate
    tokens = tokens + refill_amount
    if tokens > capacity then
        tokens = capacity
    end
end

if tokens < 1.0 then
    local usage = math.ceil(capacity - tokens)
    if usage < 0 then
        usage = 0
    end
    return {0, usage}
end

tokens = tokens - 1.0
redis.call('HSET', key, 'tokens', string.format('%.6f', tokens))
redis.call('HSET', key, 'last_refill_ms', now_ms)
redis.call('EXPIRE', key, ttl)
local usage = math.ceil(capacity - tokens)
if usage < 0 then
    usage = 0
end
return {1, usage}
"""

FIXED_WINDOW_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_duration = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local time_res = redis.call('TIME')
local now = tonumber(time_res[1])

local count = tonumber(redis.call('HGET', key, 'count'))
local window_start = tonumber(redis.call('HGET', key, 'window_start'))

if count == nil then
    count = 0
    window_start = now
else
    if now - window_start >= window_duration then
        count = 0
        window_start = now
    end
end

if count >= limit then
    return {0, count}
end

count = count + 1
redis.call('HSET', key, 'count', count)
redis.call('HSET', key, 'window_start', window_start)
redis.call('EXPIRE', key, ttl)
return {1, count}
"""

SLIDING_WINDOW_COUNTER_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_duration = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local time_res = redis.call('TIME')
local now = tonumber(time_res[1])
local current_window_start = now - (now % window_duration)

local previous_count = tonumber(redis.call('HGET', key, 'previous_count')) or 0
local current_count = tonumber(redis.call('HGET', key, 'current_count')) or 0
local window_start = tonumber(redis.call('HGET', key, 'window_start'))

if window_start == nil then
    window_start = current_window_start
elseif current_window_start > window_start then
    local elapsed_windows = math.floor((current_window_start - window_start) / window_duration)
    if elapsed_windows == 1 then
        previous_count = current_count
    else
        previous_count = 0
    end
    current_count = 0
    window_start = current_window_start
end

local elapsed = now - window_start
local weight = (window_duration - elapsed) / window_duration
if weight < 0 then
    weight = 0
end
local effective_count = current_count + (previous_count * weight)

if effective_count >= limit then
    return {0, math.ceil(effective_count)}
end

current_count = current_count + 1
redis.call('HSET', key, 'previous_count', previous_count)
redis.call('HSET', key, 'current_count', current_count)
redis.call('HSET', key, 'window_start', window_start)
redis.call('EXPIRE', key, ttl)
local updated_effective_count = current_count + (previous_count * weight)
return {1, math.ceil(updated_effective_count)}
"""


class RateLimiter:
    """Evaluates rate limits against Redis using Lua scripts for atomicity."""
    """Evaluates rate limits against Redis using Lua scripts for atomicity."""
    def __init__(self, redis_client: RedisClient, config: AppConfig):
        self.redis = redis_client
        self.config = config
        self.lua_hash_token_bucket: Optional[str] = None
        self.lua_hash_fixed_window: Optional[str] = None
        self.lua_hash_sliding_window_counter: Optional[str] = None
        self.use_lua = config.limits.ratelimit_use_lua

    async def init_scripts(self) -> None:
        if not self.use_lua:
            logger.info("Rate limit scripts disabled by config (ratelimit_use_lua=false)")
            return

        try:
            self.lua_hash_token_bucket = await self.redis.script_load(TOKEN_BUCKET_LUA)
            self.lua_hash_fixed_window = await self.redis.script_load(FIXED_WINDOW_LUA)
            self.lua_hash_sliding_window_counter = await self.redis.script_load(SLIDING_WINDOW_COUNTER_LUA)
            logger.info("Lua rate limit scripts loaded successfully")
        except Exception as exc:
            logger.warning("Failed to load Lua scripts, fallback to EVAL mode: %s", exc)

    async def check_limit(self, sender: str, limits: List[RateLimit], recipient: str) -> Tuple[bool, str, Optional[Exception]]:
        info_parts: list[str] = []

        for limit in limits:
            if limit.unlimited:
                info_parts.append("usage=unlimited")
                continue

            try:
                allowed, usage = await self._check_single_limit(sender, limit)
            except Exception as exc:
                logger.error("Rate limit check failed sender=%s recipient=%s: %s", sender, recipient, exc)
                return False, "", exc

            info = f"usage={usage}/{limit.count}"
            if limit.algorithm == "token_bucket":
                info += f", rate=@{limit.refill_rate:.4f}/s"
            elif limit.algorithm == "sliding_window_counter":
                info += ", algorithm=sliding_window_counter"
            info_parts.append(info)

            if not allowed:
                return (
                    False,
                    f"Rate limit exceeded: {limit.count} messages per {limit.duration}s (current: {usage})",
                    None,
                )

        return True, "; ".join(info_parts), None

    async def _check_single_limit(self, sender: str, limit: RateLimit) -> tuple[bool, int]:
        if limit.algorithm == "fixed_window":
            return await self._check_fixed_window(sender, limit)
        if limit.algorithm == "sliding_window_counter":
            return await self._check_sliding_window_counter(sender, limit)
        return await self._check_token_bucket(sender, limit)

    async def _check_token_bucket(self, sender: str, limit: RateLimit) -> tuple[bool, int]:
        key = self._key_for_limit(sender, limit)
        args = [limit.count, limit.refill_rate, limit.duration + 60]

        if self.lua_hash_token_bucket:
            try:
                result = await self.redis.evalsha(self.lua_hash_token_bucket, [key], args)
                return self._decode_script_result(result)
            except Exception:
                pass

        result = await self.redis.eval(TOKEN_BUCKET_LUA, [key], args)
        return self._decode_script_result(result)

    async def _check_fixed_window(self, sender: str, limit: RateLimit) -> tuple[bool, int]:
        key = self._key_for_limit(sender, limit)
        args = [limit.count, limit.duration, limit.duration + 60]

        if self.lua_hash_fixed_window:
            try:
                result = await self.redis.evalsha(self.lua_hash_fixed_window, [key], args)
                return self._decode_script_result(result)
            except Exception:
                pass

        result = await self.redis.eval(FIXED_WINDOW_LUA, [key], args)
        return self._decode_script_result(result)

    async def _check_sliding_window_counter(self, sender: str, limit: RateLimit) -> tuple[bool, int]:
        key = self._key_for_limit(sender, limit)
        args = [limit.count, limit.duration, (limit.duration * 2) + 60]

        if self.lua_hash_sliding_window_counter:
            try:
                result = await self.redis.evalsha(self.lua_hash_sliding_window_counter, [key], args)
                return self._decode_script_result(result)
            except Exception:
                pass

        result = await self.redis.eval(SLIDING_WINDOW_COUNTER_LUA, [key], args)
        return self._decode_script_result(result)

    async def get_usage(self, sender: str, limit: RateLimit) -> int:
        key = self._key_for_limit(sender, limit)
        data = await self.redis.hgetall(key)
        if not data:
            return 0

        if limit.algorithm == "fixed_window":
            count = int(data.get("count", 0))
            start = int(data.get("window_start", 0))
            now = int(time.time())
            if now - start >= limit.duration:
                return 0
            return count

        if limit.algorithm == "sliding_window_counter":
            previous_count = int(data.get("previous_count", 0))
            current_count = int(data.get("current_count", 0))
            window_start = int(data.get("window_start", 0))
            now = int(time.time())
            if window_start <= 0 or limit.duration <= 0:
                return 0

            elapsed = now - window_start
            if elapsed >= limit.duration:
                elapsed_windows = elapsed // limit.duration
                if elapsed_windows == 1:
                    previous_count = current_count
                    current_count = 0
                    window_start += limit.duration
                    elapsed = now - window_start
                else:
                    return 0

            weight = max(0.0, (limit.duration - elapsed) / float(limit.duration))
            effective_count = current_count + (previous_count * weight)
            return max(0, int(math.ceil(effective_count)))

        tokens = float(data.get("tokens", limit.count))
        last_refill = int(data.get("last_refill_ms", int(time.time() * 1000)))
        now_ms = int(time.time() * 1000)

        elapsed = (now_ms - last_refill) / 1000.0
        refill = elapsed * limit.refill_rate
        tokens = min(float(limit.count), tokens + refill)
        usage = int(limit.count - tokens)
        return max(0, usage)

    async def reset_limit(self, sender: str, limit: RateLimit) -> None:
        await self.redis.delete(self._key_for_limit(sender, limit))

    @staticmethod
    def _decode_script_result(result: object) -> tuple[bool, int]:
        if isinstance(result, (list, tuple)) and len(result) >= 2:
            return int(result[0]) == 1, max(int(result[1]), 0)
        return int(result) == 1, 0

    def _key_for_limit(self, sender: str, limit: RateLimit) -> str:
        if limit.algorithm == "fixed_window":
            return f"ratelimit:window:{sender}:{limit.duration}"
        if limit.algorithm == "sliding_window_counter":
            return f"ratelimit:sliding:{sender}:{limit.duration}"
        return f"ratelimit:bucket:{sender}:{limit.duration}"
