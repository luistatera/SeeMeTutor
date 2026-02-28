"""Unit tests for security utilities."""

import pytest

from modules.security import (
    SlidingWindowRateLimiter,
    build_security_headers,
    extract_client_ip,
    parse_allowed_origins,
)


class TestOrigins:
    def test_parse_allowed_origins_from_env(self):
        origins = parse_allowed_origins(
            "https://app.example.com, http://localhost:3000,  ",
            defaults=["http://127.0.0.1:8000"],
        )
        assert origins == ["https://app.example.com", "http://localhost:3000"]

    def test_parse_allowed_origins_uses_defaults_when_empty(self):
        origins = parse_allowed_origins(" ", defaults=["http://localhost:8000"])
        assert origins == ["http://localhost:8000"]


class TestClientIp:
    def test_extract_client_ip_prefers_forwarded_first_hop(self):
        ip = extract_client_ip("203.0.113.10, 10.0.0.8", "127.0.0.1")
        assert ip == "203.0.113.10"

    def test_extract_client_ip_falls_back(self):
        ip = extract_client_ip(None, "127.0.0.1")
        assert ip == "127.0.0.1"


class TestSecurityHeaders:
    def test_headers_include_expected_basics(self):
        headers = build_security_headers(csp_enabled=True)
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Content-Security-Policy" in headers
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_csp_can_be_disabled(self):
        headers = build_security_headers(csp_enabled=False)
        assert "Content-Security-Policy" not in headers


class TestSlidingWindowRateLimiter:
    @pytest.mark.asyncio
    async def test_blocks_after_limit_and_recovers_after_window(self):
        limiter = SlidingWindowRateLimiter()

        assert await limiter.allow("client-1", limit=2, window_seconds=10, now=100.0)
        assert await limiter.allow("client-1", limit=2, window_seconds=10, now=101.0)
        assert not await limiter.allow("client-1", limit=2, window_seconds=10, now=102.0)

        # Old requests fall outside the window, allowing traffic again.
        assert await limiter.allow("client-1", limit=2, window_seconds=10, now=111.1)
