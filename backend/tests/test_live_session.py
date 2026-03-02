"""Unit tests for live session helpers."""

from modules.live_session import (
    build_live_run_config,
    compute_retry_backoff,
    extract_total_token_estimate,
)


class _FakeRunConfig:
    model_fields = {
        "context_window_compression": object(),
    }

    def __init__(self):
        self.context_window_compression = None

    def model_copy(self, update: dict):
        clone = _FakeRunConfig()
        clone.context_window_compression = self.context_window_compression
        for key, value in update.items():
            setattr(clone, key, value)
        return clone


class _FakeTypes:
    class ContextWindowCompressionConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs


class _FakeEvent:
    def model_dump(self):
        return {
            "usage": {"total_tokens": 32123},
        }


def test_build_live_run_config_applies_fields():
    run_config, meta = build_live_run_config(
        _FakeRunConfig(),
        _FakeTypes,
        compression_enabled=True,
        compression_trigger_tokens=32000,
        compression_target_tokens=16000,
    )
    assert run_config.context_window_compression is not None
    assert meta["compression_enabled"] is True


def test_extract_total_token_estimate_from_event():
    tokens = extract_total_token_estimate(_FakeEvent())
    assert tokens == 32123


def test_retry_backoff_is_increasing():
    a = compute_retry_backoff(1, 0.6)
    b = compute_retry_backoff(2, 0.6)
    c = compute_retry_backoff(3, 0.6)
    assert a < b < c
