"""
Security utilities for FastAPI runtime hardening.

Includes:
- CORS allowlist parsing from environment values
- Security header defaults
- Lightweight in-memory sliding-window rate limiter
- Client IP extraction from proxy headers
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


def parse_allowed_origins(raw: str | None, *, defaults: list[str] | None = None) -> list[str]:
    """Parse comma-separated origins into a normalized allowlist."""
    if raw is None or not str(raw).strip():
        return list(defaults or [])
    items = [origin.strip() for origin in str(raw).split(",")]
    return [origin for origin in items if origin]


def extract_client_ip(x_forwarded_for: str | None, fallback_host: str | None = None) -> str:
    """Extract the first client IP from X-Forwarded-For, with fallback."""
    if x_forwarded_for:
        first = str(x_forwarded_for).split(",")[0].strip()
        if first:
            return first
    fallback = str(fallback_host or "").strip()
    return fallback or "unknown"


def build_security_headers(*, csp_enabled: bool = True) -> dict[str, str]:
    """Return secure response headers for HTTP routes."""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(self), microphone=(self), geolocation=(), fullscreen=(self)",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-site",
    }
    if csp_enabled:
        headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' data: blob:; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self' ws: wss:; "
            "worker-src 'self' blob:"
        )
    return headers


class SlidingWindowRateLimiter:
    """Simple in-memory per-key sliding-window limiter."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        """Return True if key is under limit for the active window."""
        if limit <= 0:
            return False

        current = float(now if now is not None else time.time())
        window_start = current - max(0.001, float(window_seconds))
        safe_key = str(key or "unknown")

        async with self._lock:
            bucket = self._events[safe_key]
            while bucket and bucket[0] <= window_start:
                bucket.popleft()

            if len(bucket) >= int(limit):
                return False

            bucket.append(current)
            return True
