import asyncio
import contextlib
import ipaddress
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from policyd_py.config.settings import AppConfig, PolicyRule, RateLimit
from policyd_py.core.models import PolicyRequest, PolicyResponse
from policyd_py.ldap.client import LDAPClient
from policyd_py.notification.notifier import Notifier
from policyd_py.policy.matcher import PolicyMatcher
from policyd_py.ratelimit.limiter import RateLimiter
from policyd_py.stats.runtime import RuntimeStats
from policyd_py.storage.redis_client import RedisClient
from policyd_py.validation.validator import EmailValidator

if TYPE_CHECKING:
    from policyd_py.actions.manager import ActionManager

logger = logging.getLogger(__name__)


@dataclass
class _BackgroundEvent:
    kind: str
    email: str = ""
    selector: str = ""
    message: str = ""
    local_sender: bool = False
    limits: list[RateLimit] | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "email": self.email,
            "selector": self.selector,
            "message": self.message,
            "local_sender": self.local_sender,
            "limits": [L.model_dump() for L in self.limits] if self.limits else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "_BackgroundEvent":
        limits_data = data.get("limits")
        limits = [RateLimit(**L) for L in limits_data] if limits_data else None
        return cls(
            kind=data.get("kind", ""),
            email=data.get("email", ""),
            selector=data.get("selector", ""),
            message=data.get("message", ""),
            local_sender=data.get("local_sender", False),
            limits=limits,
        )


class PolicyHandler:
    """Main policy evaluation engine.

    Receives ``PolicyRequest`` objects from the Postfix delegation socket,
    validates email syntax, checks blacklists, resolves policies, applies
    rate limits (with adaptive multipliers and progressive penalties), and
    triggers lock/unlock actions and notifications when thresholds are exceeded.
    """
    """Main policy evaluation engine.

    Receives ``PolicyRequest`` objects from the Postfix delegation socket,
    validates email syntax, checks blacklists, resolves policies, applies
    rate limits (with adaptive multipliers and progressive penalties), and
    triggers lock/unlock actions and notifications when thresholds are exceeded.
    """
    def __init__(
        self,
        config: AppConfig,
        redis_client: RedisClient,
        ratelimit_engine: RateLimiter,
        validator: EmailValidator,
        matcher: PolicyMatcher,
        notifier: Notifier,
        ldap_client: Optional[LDAPClient] = None,
        action_manager: Optional["ActionManager"] = None,
        stats: Optional[RuntimeStats] = None,
    ):
        self.config = config
        self.redis = redis_client
        self.ratelimit_engine = ratelimit_engine
        self.validator = validator
        self.matcher = matcher
        self.notifier = notifier
        self.ldap_client = ldap_client
        self.action_manager = action_manager

        self.stats = stats or RuntimeStats()

        self._unlock_listener_task: Optional[asyncio.Task] = None
        self._local_domains: set[str] = set()
        self._shutdown_event = asyncio.Event()
        self._background_workers: list[asyncio.Task] = []
        self._background_workers_started = False

    async def start(self) -> None:
        """Start validators, load lists to Redis, and launch background workers."""
        await self.validator.start()
        await self._load_lists_to_redis()
        await self._start_background_workers()
        await self._restart_unlock_listener()

    async def stop(self) -> None:
        """Shut down all background tasks, validators, and the action manager."""
        """Shut down all background tasks, validators, and the action manager."""
        await self._stop_unlock_listener()
        await self._stop_background_workers()
        await self.validator.stop()
        if self.action_manager:
            await self.action_manager.close()

    async def reload_lists(self) -> None:
        """Reload domain/account/IP lists from config into Redis."""
        await self._load_lists_to_redis()

    async def refresh_runtime_dependencies(
        self,
        config: AppConfig,
        ldap_client: Optional[LDAPClient],
        action_manager: Optional["ActionManager"],
    ) -> None:
        """Hot-reload runtime dependencies after a config change."""
        self.config = config
        self.ldap_client = ldap_client
        self.action_manager = action_manager
        await self._restart_unlock_listener()

    def on_connection_open(self) -> None:
        """Track a new Postfix client connection for stats."""
        self.stats.inc_active_connections()

    def on_connection_close(self) -> None:
        """Track a closed Postfix client connection for stats."""
        self.stats.dec_active_connections()

    def get_stats_snapshot(self):
        """Return a snapshot of runtime statistics counters."""
        """Return a snapshot of runtime statistics counters."""
        return self.stats.snapshot().to_dict()

    async def get_runtime_state(self, email: str) -> dict:
        lock_key = f"lock:{email}"
        penalty_key = f"penalty:{email.lower()}"
        lock_exists = bool(await self.redis.exists(lock_key))
        penalty_exists = bool(await self.redis.exists(penalty_key))

        usage = []
        for limit in self._all_configured_limits():
            if limit.unlimited:
                continue
            current_usage = await self.ratelimit_engine.get_usage(email, limit)
            usage.append(
                {
                    "algorithm": limit.algorithm,
                    "duration": limit.duration,
                    "count": limit.count,
                    "usage": current_usage,
                    "exceeded": current_usage >= limit.count,
                }
            )

        return {
            "email": email,
            "locked": lock_exists,
            "lock_reason": await self.redis.get(lock_key) if lock_exists else None,
            "lock_ttl_seconds": await self.redis.ttl(lock_key) if lock_exists else None,
            "penalty_count": int(await self.redis.get(penalty_key) or 0) if penalty_exists else 0,
            "penalty_ttl_seconds": await self.redis.ttl(penalty_key) if penalty_exists else None,
            "adaptive_enabled": self.config.adaptive.enable,
            "penalty_enabled": self.config.penalty.enable,
            "usage": usage,
        }

    async def lock_user_manually(self, email: str, reason: str = "manual") -> None:
        await self._safe_lock_user(email, reason)

    async def unlock_user_manually(self, email: str) -> None:
        await self._process_unlock_event(email, force=True, clear_lock=True)

    async def reset_ratelimit_manually(self, email: str) -> None:
        limits: list[RateLimit] = []
        limits.extend(self.config.limits.default_quota)
        for quota_limits in self.config.limits.quotas.values():
            limits.extend(quota_limits)

        seen: set[tuple[str, int, int, float, bool]] = set()
        for limit in limits:
            fingerprint = (limit.algorithm, limit.duration, limit.count, limit.refill_rate, limit.unlimited)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            if limit.unlimited:
                continue
            await self.ratelimit_engine.reset_limit(email, limit)

    async def reset_penalty_manually(self, email: str) -> None:
        metadata = await self._load_lock_metadata(email)
        selectors = {email.lower()}
        if metadata and metadata.get("selector"):
            selectors.add(str(metadata["selector"]).lower())
        for selector in selectors:
            await self.redis.delete(f"penalty:{selector}")

    async def handle(self, req: PolicyRequest) -> PolicyResponse:
        """Evaluate a single Postfix policy request and return an action response.

        This is the core entry point called for every SMTP RCPT TO event.
        """
        self.stats.inc_requests()

        try:
            if not self.config.limits.enable_policyd:
                self.stats.inc_accepted()
                return PolicyResponse.create_dunno()

            if req.protocol_state != self.config.limits.policy_check_state:
                self.stats.inc_accepted()
                return PolicyResponse.create_dunno()

            sender = req.sender or ""
            recipient = req.recipient or ""

            ok, msg = self.validator.validate_sender_syntax(sender)
            if not ok:
                return self._validation_response(msg, blacklisted=False)

            ok, msg = self.validator.validate_recipient_syntax(recipient)
            if not ok:
                return self._validation_response(msg, blacklisted=False)

            ok, msg = await self.validator.validate_recipient_deliverability(recipient)
            if not ok:
                return self._validation_response(msg, blacklisted=False)

            blocked, msg = self.validator.check_sender_blacklist(sender)
            if blocked:
                return self._validation_response(msg, blacklisted=True)

            blocked, msg = self.validator.check_recipient_blacklist(recipient)
            if blocked:
                return self._validation_response(msg, blacklisted=True)

            blocked, msg = self.validator.check_domain_blacklist(req.sender_domain)
            if blocked:
                return self._validation_response(msg, blacklisted=True)

            policy = await self.matcher.match(req)
            limits = self._resolve_limits(policy)

            if not limits:
                self.stats.inc_accepted()
                return PolicyResponse.create_dunno()

            selector = self._selector(req)
            if not selector:
                self.stats.inc_accepted()
                return PolicyResponse.create_dunno()

            limits = await self._apply_adaptive_limits(req, selector, limits)
            if limits and limits[0].unlimited:
                self.stats.inc_accepted()
                return PolicyResponse.create_dunno()

            if any(limit.algorithm == "sliding_window_counter" for limit in limits if not limit.unlimited):
                self.stats.inc_sliding_window_checks()

            allowed, info, err = await self.ratelimit_engine.check_limit(selector, limits, recipient)
            if err:
                logger.error("Rate limit check error: %s", err)
                self.stats.inc_errors()
                return PolicyResponse(action=self.config.actions.db_error_action)

            if not allowed:
                self.stats.inc_rate_limited()
                self.stats.inc_deferred()
                await self._handle_rate_limit_exceeded(req, selector, info, limits)
                return PolicyResponse.create_defer(info)

            self.stats.inc_accepted()
            return PolicyResponse.create_dunno()

        except Exception as exc:
            logger.error("Error handling policy request: %s", exc, exc_info=True)
            self.stats.inc_errors()
            return PolicyResponse(action=self.config.actions.db_error_action)

    def _selector(self, req: PolicyRequest) -> str:
        return req.sasl_username or req.sender or ""

    def _resolve_limits(self, policy: Optional[PolicyRule]) -> list[RateLimit]:
        if policy is None:
            return self.config.limits.default_quota

        quota_name = policy.quota.lower()
        if quota_name in ("unlimited", "nolimit"):
            return [RateLimit(unlimited=True)]

        return self.config.limits.quotas.get(policy.quota, self.config.limits.default_quota)

    def _validation_response(self, message: str, blacklisted: bool) -> PolicyResponse:
        if blacklisted:
            self.stats.inc_blacklisted()
        else:
            self.stats.inc_validation_errors()

        if self.config.validation.monitor_only:
            logger.warning("Validation/blacklist hit in monitor_only mode: %s", message)
            self.stats.inc_accepted()
            return PolicyResponse.create_dunno()

        self.stats.inc_rejected()
        return PolicyResponse.create_reject(message)

    async def _handle_rate_limit_exceeded(
        self,
        req: PolicyRequest,
        selector: str,
        message: str,
        limits: list[RateLimit],
    ) -> None:
        if self._is_local_domain(req.sender_domain):
            await self._enqueue_background_event(
                _BackgroundEvent(
                    kind="lock",
                    email=req.sender or "",
                    selector=selector,
                    message=message,
                    local_sender=True,
                    limits=[limit.model_copy() for limit in limits],
                ),
                drop_if_full=False,
            )

        await self._enqueue_background_event(
            _BackgroundEvent(kind="notify", selector=selector, message=message),
            drop_if_full=True,
        )

    async def _safe_notify(self, selector: str, message: str) -> None:
        try:
            notify_key = f"notify:{selector}"
            exists = await self.redis.exists(notify_key)
            if exists:
                return

            cooldown = max(self.config.locks.lock_duration, 3600)
            await self.redis.set(notify_key, "sent", cooldown)
            await self.notifier.notify_rate_limit(selector, message)
        except Exception as exc:
            logger.warning("Failed to send rate limit notification for %s: %s", selector, exc)

    async def _safe_lock_user_with_penalty(
        self,
        email: str,
        selector: str,
        reason: str,
        limits: Optional[list[RateLimit]] = None,
    ) -> None:
        if not email:
            return

        lock_key = f"lock:{email}"
        if await self.redis.exists(lock_key):
            return

        lock_duration = await self._resolve_lock_duration(selector)
        await self._safe_lock_user(
            email,
            reason,
            lock_duration=lock_duration,
            selector=selector,
            limits=limits,
        )

    async def _resolve_lock_duration(self, selector: str) -> int:
        default_duration = max(1, self.config.locks.lock_duration)
        if not self.config.penalty.enable or not selector:
            return default_duration

        steps = self.config.penalty.steps or [default_duration]
        penalty_key = f"penalty:{selector.lower()}"
        count = await self.redis.incr(penalty_key)
        await self.redis.expire(penalty_key, max(1, self.config.penalty.ttl))

        self.stats.inc_penalty_applied()
        index = min(max(count - 1, 0), len(steps) - 1)
        duration = max(1, steps[index])
        if index > 0:
            self.stats.inc_penalty_escalations()
        return duration

    async def _safe_lock_user(
        self,
        email: str,
        reason: str,
        lock_duration: Optional[int] = None,
        selector: Optional[str] = None,
        limits: Optional[list[RateLimit]] = None,
    ) -> None:
        if not email:
            return

        applied_duration = max(1, lock_duration or self.config.locks.lock_duration)

        try:
            lock_key = f"lock:{email}"
            acquired = await self.redis.setnx(lock_key, reason, applied_duration)
            if not acquired:
                return
            await self._store_lock_metadata(email, selector or email, limits, applied_duration)

            if self._needs_unlock_listener():
                unlock_time = int(time.time()) + applied_duration
                await self.redis.zadd("policyd:scheduled_unlocks", {f"lock:{email}": unlock_time})

            self.stats.inc_users_locked()

            if self.action_manager:
                await self.action_manager.lock_account(email, reason)

            await self.notifier.notify_user_locked(email, applied_duration)
        except Exception as exc:
            logger.error("Failed to lock user %s: %s", email, exc)

    def _needs_unlock_listener(self) -> bool:
        return bool(self.action_manager)

    async def _stop_unlock_listener(self) -> None:
        if self._unlock_listener_task:
            self._unlock_listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._unlock_listener_task
            self._unlock_listener_task = None

    async def _restart_unlock_listener(self) -> None:
        await self._stop_unlock_listener()
        if self._needs_unlock_listener():
            self._unlock_listener_task = asyncio.create_task(self._listen_for_unlocks())

    async def _listen_for_unlocks(self) -> None:
        try:
            while not self._shutdown_event.is_set():
                now = int(time.time())
                try:
                    items = await self.redis.zrangebyscore("policyd:scheduled_unlocks", "-inf", now)
                    for item in items:
                        if not isinstance(item, str):
                            item = item.decode('utf-8') if isinstance(item, bytes) else str(item)
                            
                        removed = await self.redis.zrem("policyd:scheduled_unlocks", [item])
                        if removed and item.startswith("lock:"):
                            email = item[len("lock:"):]
                            await self._enqueue_background_event(
                                _BackgroundEvent(kind="unlock", email=email),
                                drop_if_full=False,
                            )
                except Exception as exc:
                    if not self._shutdown_event.is_set():
                        logger.error("Error polling scheduled unlocks: %s", exc)
                
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass

    async def _start_background_workers(self) -> None:
        if self._background_workers_started:
            return

        worker_count = min(max(self.config.general.worker_count // 10, 2), 32)
        self._shutdown_event.clear()
        self._background_workers = [
            asyncio.create_task(self._background_worker(idx))
            for idx in range(worker_count)
        ]
        self._background_workers_started = True

    async def _stop_background_workers(self) -> None:
        if not self._background_workers_started:
            return

        self._shutdown_event.set()
        for task in self._background_workers:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self._background_workers.clear()
        self._background_workers_started = False

    async def _background_worker(self, idx: int) -> None:
        while not self._shutdown_event.is_set():
            try:
                res = await self.redis.brpop("policyd:bg_tasks", timeout=1)
                if not res:
                    continue
                
                _, payload = res
                event = _BackgroundEvent.from_dict(json.loads(payload))

                if event.kind == "lock":
                    await self._safe_lock_user_with_penalty(event.email, event.selector, event.message, event.limits)
                elif event.kind == "notify":
                    await self._safe_notify(event.selector, event.message)
                elif event.kind == "unlock":
                    await self._process_unlock_event(event.email)
                else:
                    logger.warning("Unknown background event kind=%s worker=%s", event.kind, idx)
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                if not self._shutdown_event.is_set():
                    logger.error("Background worker %s error=%s", idx, exc, exc_info=True)
                    await asyncio.sleep(1.0)

    async def _enqueue_background_event(self, event: _BackgroundEvent, drop_if_full: bool) -> None:
        try:
            payload = json.dumps(event.to_dict())
            await self.redis.lpush("policyd:bg_tasks", payload)
        except Exception as exc:
            logger.error("Failed to enqueue background event kind=%s: %s", event.kind, exc)
            if event.kind == "lock":
                await self._safe_lock_user_with_penalty(event.email, event.selector, event.message, event.limits)
            elif event.kind == "unlock":
                await self._process_unlock_event(event.email)

    async def _process_unlock_event(self, email: str, force: bool = False, clear_lock: bool = False) -> None:
        if not self.action_manager:
            if clear_lock:
                with contextlib.suppress(Exception):
                    await self.redis.delete(f"lock:{email}")
                    await self.redis.delete(f"lockmeta:{email}")
            return

        try:
            metadata = await self._load_lock_metadata(email)
            if not force and await self._is_still_exceeding_limit(
                email,
                selector=metadata.get("selector") if metadata else email,
                limits=self._limits_from_metadata(metadata),
            ):
                await self.redis.set(f"lock:{email}", "Extended: still exceeding limit", self.config.locks.lock_duration)
                if metadata:
                    await self._store_lock_metadata(
                        email,
                        metadata.get("selector") or email,
                        self._limits_from_metadata(metadata),
                        self.config.locks.lock_duration,
                    )
                return

            if self.action_manager:
                await self.action_manager.unlock_account(email)

            if clear_lock:
                with contextlib.suppress(Exception):
                    await self.redis.delete(f"lock:{email}")
                    await self.redis.delete(f"lockmeta:{email}")
                    if self._needs_unlock_listener():
                        await self.redis.zrem("policyd:scheduled_unlocks", [f"lock:{email}"])

            await self.notifier.notify_user_unlocked(email)
        except Exception as exc:
            logger.error("Failed to process unlock for %s: %s", email, exc)
            with contextlib.suppress(Exception):
                await self.redis.set(f"lock:{email}", "Retry unlock", 60)

    async def _is_still_exceeding_limit(
        self,
        email: str,
        selector: Optional[str] = None,
        limits: Optional[list[RateLimit]] = None,
    ) -> bool:
        effective_selector = selector or email
        limits = limits or self.config.limits.default_quota
        for limit in limits:
            if limit.unlimited:
                continue
            usage = await self.ratelimit_engine.get_usage(effective_selector, limit)
            if usage >= limit.count:
                return True
        return False

    async def _store_lock_metadata(
        self,
        email: str,
        selector: str,
        limits: Optional[list[RateLimit]],
        ttl_seconds: int,
    ) -> None:
        payload = {
            "selector": selector.lower(),
            "limits": [limit.model_dump() for limit in (limits or self.config.limits.default_quota)],
        }
        await self.redis.set(f"lockmeta:{email}", json.dumps(payload), ttl_seconds)

    async def _load_lock_metadata(self, email: str) -> Optional[dict]:
        raw = await self.redis.get(f"lockmeta:{email}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _limits_from_metadata(self, metadata: Optional[dict]) -> Optional[list[RateLimit]]:
        if not metadata:
            return None
        raw_limits = metadata.get("limits")
        if not isinstance(raw_limits, list):
            return None
        limits: list[RateLimit] = []
        for item in raw_limits:
            if not isinstance(item, dict):
                continue
            limits.append(RateLimit(**item))
        return limits or None

    async def _apply_adaptive_limits(self, req: PolicyRequest, selector: str, limits: list[RateLimit]) -> list[RateLimit]:
        if not self.config.adaptive.enable:
            return limits

        multiplier = await self._resolve_adaptive_multiplier(req, selector)
        if math.isclose(multiplier, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            return limits

        self.stats.inc_adaptive_adjustments()
        if multiplier > 1.0:
            self.stats.inc_adaptive_relaxed()
        else:
            self.stats.inc_adaptive_tightened()

        scaled_limits: list[RateLimit] = []
        for limit in limits:
            if limit.unlimited:
                scaled_limits.append(limit)
                continue

            scaled_count = max(1, int(math.ceil(limit.count * multiplier)))
            refill_rate = limit.refill_rate
            if limit.algorithm == "token_bucket" and refill_rate > 0:
                refill_rate = max(refill_rate * multiplier, 1.0 / float(limit.duration or 1))

            scaled_limits.append(
                RateLimit(
                    count=scaled_count,
                    duration=limit.duration,
                    refill_rate=refill_rate,
                    algorithm=limit.algorithm,
                    unlimited=limit.unlimited,
                )
            )
        return scaled_limits

    async def _resolve_adaptive_multiplier(self, req: PolicyRequest, selector: str) -> float:
        cfg = self.config.adaptive
        positive: list[float] = []
        negative: list[float] = []

        def collect(value: float) -> None:
            if value <= 0:
                return
            if value > 1.0:
                positive.append(value)
            elif value < 1.0:
                negative.append(value)

        if req.sasl_username:
            collect(cfg.authenticated_multiplier)
        else:
            collect(cfg.unauthenticated_multiplier)

        if self._is_local_domain(req.sender_domain):
            collect(cfg.local_sender_multiplier)
        elif req.sender_domain:
            collect(cfg.external_sender_multiplier)

        if await self._is_trusted_request(req, selector):
            collect(cfg.trusted_multiplier)

        multiplier = max([1.0] + positive) if positive else min([1.0] + negative) if negative else 1.0
        return min(cfg.maximum_multiplier, max(cfg.minimum_multiplier, multiplier))

    async def _is_trusted_request(self, req: PolicyRequest, selector: str) -> bool:
        values = [selector.lower()]
        if req.sender:
            values.append(req.sender.lower())

        if values and await self._is_member_of_named_sets(values, self.config.adaptive.trusted_account_lists):
            return True
        if req.sender_domain and await self._is_member_of_named_sets(
            [req.sender_domain.lower()], self.config.adaptive.trusted_domain_lists
        ):
            return True
        if req.client_address and self._ip_matches_named_lists(req.client_address, self.config.adaptive.trusted_ip_lists):
            return True
        return False

    async def _is_member_of_named_sets(self, values: list[str], list_names: list[str]) -> bool:
        for list_name in list_names:
            key = f"policyd:list:{list_name}"
            for value in values:
                if value and await self.redis.sismember(key, value):
                    return True
        return False

    def _ip_matches_named_lists(self, ip_value: str, list_names: list[str]) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip_value)
        except ValueError:
            return False

        for list_name in list_names:
            entries = self.config.limits.ip_lists.get(list_name, [])
            for entry in entries:
                candidate = entry.strip()
                if not candidate:
                    continue
                with contextlib.suppress(ValueError):
                    if "/" in candidate and ip_obj in ipaddress.ip_network(candidate, strict=False):
                        return True
                    if "/" not in candidate and ip_obj == ipaddress.ip_address(candidate):
                        return True
        return False

    def _all_configured_limits(self) -> list[RateLimit]:
        seen: set[tuple[str, int, int, float, bool]] = set()
        collected: list[RateLimit] = []
        for limit in self.config.limits.default_quota:
            fingerprint = (limit.algorithm, limit.duration, limit.count, limit.refill_rate, limit.unlimited)
            if fingerprint not in seen:
                seen.add(fingerprint)
                collected.append(limit)
        for quota_limits in self.config.limits.quotas.values():
            for limit in quota_limits:
                fingerprint = (limit.algorithm, limit.duration, limit.count, limit.refill_rate, limit.unlimited)
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    collected.append(limit)
        return collected

    def _is_local_domain(self, domain: str) -> bool:
        if not domain:
            return False
        return domain.lower() in self._local_domains

    async def _load_lists_to_redis(self) -> None:
        self._local_domains = set()

        async def resolve_members(members: list[str]) -> list[str]:
            result: list[str] = []
            for member in members:
                if not member:
                    continue
                if member.startswith("__LDAP__:"):
                    ldap_uri = member[len("__LDAP__:") :]
                    if not self.ldap_client:
                        logger.warning("LDAP marker found but LDAP client is not configured: %s", ldap_uri)
                        continue
                    try:
                        result.extend(await self.ldap_client.get_domains(ldap_uri))
                    except Exception as exc:
                        logger.warning("Failed to load LDAP domains from %s: %s", ldap_uri, exc)
                    continue
                result.append(member.lower())
            return sorted(set([x for x in result if x]))

        async def load_section(items: dict[str, list[str]], collect_local: bool = False) -> None:
            for list_name, members in items.items():
                key = f"policyd:list:{list_name}"
                await self.redis.delete(key)
                normalized = await resolve_members(members)
                if normalized:
                    await self.redis.sadd(key, normalized)
                    if collect_local:
                        self._local_domains.update(normalized)

        await load_section(self.config.limits.domain_lists, collect_local=True)
        await load_section(self.config.limits.account_lists)
        await load_section(self.config.limits.ip_lists)
