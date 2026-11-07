"""Real IP resolution, slowapi rate limiter, and in-memory abuse tracker.

Can be turned on/off via `RATE_LIMIT_ENABLED=true/false` in .env.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("security")


def get_real_ip(request: Request) -> str:
    """Extract real client IP behind Cloudflare, Vercel, or Render reverse proxies."""
    # 1. Cloudflare Connecting IP
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    # 2. Standard X-Forwarded-For header (first address is client)
    x_forwarded = request.headers.get("x-forwarded-for")
    if x_forwarded:
        first_ip = x_forwarded.split(",")[0].strip()
        if first_ip:
            return first_ip

    # 3. Nginx / Proxy X-Real-IP
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # 4. Direct socket address fallback
    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


# Configure slowapi limiter with toggle support
limiter = Limiter(
    key_func=get_real_ip,
    enabled=settings.rate_limit_enabled,
    default_limits=[settings.rate_limit_global] if settings.rate_limit_enabled else [],
    storage_uri="memory://",
)


def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Standardized JSON response when an IP hits the rate limit ceiling."""
    ip = get_real_ip(request)
    log.warning("rate_limit_exceeded", ip=ip, path=request.url.path, limit=str(exc.detail))
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": {
                "code": "rate_limited",
                "message": "Too many requests from your IP. Please wait a moment before trying again.",
                "detail": str(exc.detail),
                "ip": ip,
            }
        },
        headers={"Retry-After": "60"},
    )


class AbuseTracker:
    """Lightweight in-memory tracker that detects bursts and temporarily jails abusive IPs."""

    def __init__(self, burst_threshold: int = 70, window_seconds: int = 30, jail_seconds: int = 600):
        self.burst_threshold = burst_threshold
        self.window_seconds = window_seconds
        self.jail_seconds = jail_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)
        self.jailed: dict[str, float] = {}

    def is_jailed(self, ip: str) -> bool:
        if not settings.rate_limit_enabled:
            return False
        jail_expiry = self.jailed.get(ip)
        if jail_expiry:
            if time.time() < jail_expiry:
                return True
            del self.jailed[ip]
        return False

    def record_and_check(self, ip: str) -> bool:
        """Record a hit. Return False if IP exceeded burst limit and is now jailed."""
        if not settings.rate_limit_enabled:
            return True

        now = time.time()
        # Clean older requests outside the sliding window
        window_start = now - self.window_seconds
        timestamps = [t for t in self.requests[ip] if t > window_start]
        timestamps.append(now)
        self.requests[ip] = timestamps

        if len(timestamps) > self.burst_threshold:
            self.jailed[ip] = now + self.jail_seconds
            log.warning("ip_auto_jailed_for_burst_abuse", ip=ip, count=len(timestamps), duration_sec=self.jail_seconds)
            return False

        return True


abuse_tracker = AbuseTracker()

