"""Tests for gateway auth: API key, HMAC, rate limiter, masking."""

import time

from gateway.auth import (
    HMACVerifier,
    RateLimiter,
    mask_secret,
    sanitize_log_message,
)


class TestMaskSecret:
    def test_normal_secret(self):
        assert mask_secret("sk-abc123xyz789") == "sk-a...z789"

    def test_short_value(self):
        result = mask_secret("ab")
        assert "..." in result

    def test_empty_string(self):
        assert mask_secret("") == ""

    def test_single_char(self):
        assert mask_secret("x") == "*"

    def test_custom_visible(self):
        result = mask_secret("abcdefghijklmnop", visible=6)
        assert result.startswith("abcdef")
        assert result.endswith("mnop")


class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = RateLimiter(max_requests=3, window_seconds=60)
        assert limiter.is_allowed("1.2.3.4") is True
        assert limiter.is_allowed("1.2.3.4") is True
        assert limiter.is_allowed("1.2.3.4") is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_requests=2, window_seconds=60)
        limiter.is_allowed("1.2.3.4")
        limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4") is False

    def test_different_ips_independent(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.is_allowed("1.1.1.1") is True
        assert limiter.is_allowed("2.2.2.2") is True
        assert limiter.is_allowed("1.1.1.1") is False

    def test_localhost_exempt(self):
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        assert limiter.is_allowed("127.0.0.1") is True
        assert limiter.is_allowed("127.0.0.1") is True
        assert limiter.is_allowed("::1") is True

    def test_window_expiry(self):
        limiter = RateLimiter(max_requests=1, window_seconds=1)
        limiter.is_allowed("1.2.3.4")
        assert limiter.is_allowed("1.2.3.4") is False
        time.sleep(1.1)
        assert limiter.is_allowed("1.2.3.4") is True


class TestHMACVerifier:
    def test_no_secret_configured(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_HMAC_SECRET", raising=False)
        assert HMACVerifier.verify(b"payload", "any") is True

    def test_valid_signature(self, monkeypatch):
        import hashlib
        import hmac as hmac_mod

        monkeypatch.setenv("WEBHOOK_HMAC_SECRET", "test-secret")
        payload = b'{"test": true}'
        expected = hmac_mod.new(b"test-secret", payload, hashlib.sha256).hexdigest()
        assert HMACVerifier.verify(payload, f"sha256={expected}") is True

    def test_invalid_signature(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_HMAC_SECRET", "test-secret")
        assert HMACVerifier.verify(b"payload", "sha256=wrong") is False


class TestSanitizeLogMessage:
    def test_masks_known_secrets(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_KEY", "zhipu-secret-123")
        result = sanitize_log_message("Error with key zhipu-secret-123 in request")
        assert "zhipu-secret-123" not in result
        assert "..." in result

    def test_no_secrets_present(self):
        result = sanitize_log_message("Normal log message")
        assert result == "Normal log message"
