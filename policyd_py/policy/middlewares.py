"""Legacy compatibility module.

The new policyd-py pipeline has moved validation/policy matching/rate-limit logic
into PolicyHandler. This module keeps a small surface for older imports.
"""

from policyd_py.config.settings import RateLimit, parse_rate_limits


class ValidationMiddleware:  # pragma: no cover
    def __init__(self, *args, **kwargs):
        raise RuntimeError("ValidationMiddleware is deprecated. Use PolicyHandler directly.")


class RateLimitMiddleware:  # pragma: no cover
    def __init__(self, *args, **kwargs):
        raise RuntimeError("RateLimitMiddleware is deprecated. Use PolicyHandler directly.")



def parse_rate_limit(value: str) -> RateLimit:
    limits = parse_rate_limits(value)
    if not limits:
        return RateLimit(count=10, duration=3600, refill_rate=0.0, algorithm="fixed_window")
    return limits[0]
