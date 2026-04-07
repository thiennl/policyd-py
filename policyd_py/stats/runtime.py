import threading
import time
from dataclasses import asdict, dataclass


@dataclass
class StatsSnapshot:
    start_time: float
    uptime_seconds: float
    total_requests: int
    total_accepted: int
    total_rejected: int
    total_deferred: int
    total_errors: int
    active_connections: int
    total_validation_errors: int
    total_blacklisted: int
    total_rate_limited: int
    total_users_locked: int
    total_sliding_window_checks: int
    total_adaptive_adjustments: int
    total_adaptive_tightened: int
    total_adaptive_relaxed: int
    total_penalty_applied: int
    total_penalty_escalations: int

    def to_dict(self):
        return asdict(self)


class RuntimeStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.start_time = time.time()
        self.total_requests = 0
        self.total_accepted = 0
        self.total_rejected = 0
        self.total_deferred = 0
        self.total_errors = 0
        self.active_connections = 0
        self.total_validation_errors = 0
        self.total_blacklisted = 0
        self.total_rate_limited = 0
        self.total_users_locked = 0
        self.total_sliding_window_checks = 0
        self.total_adaptive_adjustments = 0
        self.total_adaptive_tightened = 0
        self.total_adaptive_relaxed = 0
        self.total_penalty_applied = 0
        self.total_penalty_escalations = 0

    def inc_requests(self):
        with self._lock:
            self.total_requests += 1

    def inc_accepted(self):
        with self._lock:
            self.total_accepted += 1

    def inc_rejected(self):
        with self._lock:
            self.total_rejected += 1

    def inc_deferred(self):
        with self._lock:
            self.total_deferred += 1

    def inc_errors(self):
        with self._lock:
            self.total_errors += 1

    def inc_validation_errors(self):
        with self._lock:
            self.total_validation_errors += 1

    def inc_blacklisted(self):
        with self._lock:
            self.total_blacklisted += 1

    def inc_rate_limited(self):
        with self._lock:
            self.total_rate_limited += 1

    def inc_users_locked(self):
        with self._lock:
            self.total_users_locked += 1

    def inc_sliding_window_checks(self):
        with self._lock:
            self.total_sliding_window_checks += 1

    def inc_adaptive_adjustments(self):
        with self._lock:
            self.total_adaptive_adjustments += 1

    def inc_adaptive_tightened(self):
        with self._lock:
            self.total_adaptive_tightened += 1

    def inc_adaptive_relaxed(self):
        with self._lock:
            self.total_adaptive_relaxed += 1

    def inc_penalty_applied(self):
        with self._lock:
            self.total_penalty_applied += 1

    def inc_penalty_escalations(self):
        with self._lock:
            self.total_penalty_escalations += 1

    def inc_active_connections(self):
        with self._lock:
            self.active_connections += 1

    def dec_active_connections(self):
        with self._lock:
            if self.active_connections > 0:
                self.active_connections -= 1

    def snapshot(self) -> StatsSnapshot:
        with self._lock:
            now = time.time()
            return StatsSnapshot(
                start_time=self.start_time,
                uptime_seconds=now - self.start_time,
                total_requests=self.total_requests,
                total_accepted=self.total_accepted,
                total_rejected=self.total_rejected,
                total_deferred=self.total_deferred,
                total_errors=self.total_errors,
                active_connections=self.active_connections,
                total_validation_errors=self.total_validation_errors,
                total_blacklisted=self.total_blacklisted,
                total_rate_limited=self.total_rate_limited,
                total_users_locked=self.total_users_locked,
                total_sliding_window_checks=self.total_sliding_window_checks,
                total_adaptive_adjustments=self.total_adaptive_adjustments,
                total_adaptive_tightened=self.total_adaptive_tightened,
                total_adaptive_relaxed=self.total_adaptive_relaxed,
                total_penalty_applied=self.total_penalty_applied,
                total_penalty_escalations=self.total_penalty_escalations,
            )
