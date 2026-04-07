"""Circuit breaker pattern implementation for Redis calls."""

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open and call is rejected."""
    pass


class CircuitBreaker:
    """Circuit breaker for Redis operations.

    States:
    - CLOSED: Normal operation. Failures are counted.
    - OPEN: Circuit tripped. All calls fail immediately.
    - HALF_OPEN: Recovery phase. Limited calls allowed to test recovery.

    Transitions:
    - CLOSED -> OPEN: When failure_count >= failure_threshold
    - OPEN -> HALF_OPEN: When recovery_timeout has elapsed
    - HALF_OPEN -> CLOSED: When success_count >= success_threshold
    - HALF_OPEN -> OPEN: On any failure
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        success_threshold: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failure_count

    async def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """Execute func through the circuit breaker."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if not self._should_attempt_reset():
                    raise CircuitBreakerError(
                        f"Circuit breaker is OPEN. "
                        f"Recovery in {self._time_until_recovery():.0f}s"
                    )
                self._transition_to_half_open()

        try:
            result = await func(*args, **kwargs)
        except Exception as exc:
            await self._record_failure()
            raise

        await self._record_success()
        return result

    def _should_attempt_reset(self) -> bool:
        return (time.time() - self._last_failure_time) >= self.recovery_timeout

    def _time_until_recovery(self) -> float:
        elapsed = time.time() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    def _transition_to_half_open(self) -> None:
        logger.info(
            "Circuit breaker transitioning OPEN -> HALF_OPEN "
            "(recovery timeout elapsed)"
        )
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._transition_to_closed()
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _record_failure(self) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._transition_to_open()
            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._transition_to_open()

    def _transition_to_open(self) -> None:
        logger.warning(
            "Circuit breaker transitioning to OPEN "
            "(failures=%d, threshold=%d)",
            self._failure_count,
            self.failure_threshold,
        )
        self._state = CircuitState.OPEN

    def _transition_to_closed(self) -> None:
        logger.info(
            "Circuit breaker transitioning HALF_OPEN -> CLOSED "
            "(successes=%d, threshold=%d)",
            self._success_count,
            self.success_threshold,
        )
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0

    async def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = 0.0
        logger.info("Circuit breaker manually reset to CLOSED")

    def get_state_info(self) -> dict:
        """Return current circuit breaker state for monitoring."""
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "failure_threshold": self.failure_threshold,
            "success_threshold": self.success_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self._last_failure_time,
        }
