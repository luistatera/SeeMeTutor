"""
Live session helpers for compression and retry.

This module keeps SDK-specific logic out of main.py:
- build RunConfig with optional context compression
- extract token estimates from runner events
- compute retry backoff for stream reconnects
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


def _model_field_names(model: Any) -> set[str]:
    """Return pydantic-like field names from a model instance."""
    cls = model.__class__
    fields = getattr(cls, "model_fields", None)
    if isinstance(fields, dict):
        return set(str(k) for k in fields.keys())
    legacy_fields = getattr(cls, "__fields__", None)
    if isinstance(legacy_fields, dict):
        return set(str(k) for k in legacy_fields.keys())
    return set()


def _copy_model_with_update(model: Any, updates: dict[str, Any]) -> Any:
    """Return model copy with updates; fallback to in-place mutation."""
    if not updates:
        return model
    if hasattr(model, "model_copy"):
        return model.model_copy(update=updates)
    if hasattr(model, "copy"):
        try:
            return model.copy(update=updates)
        except Exception:
            pass
    for key, value in updates.items():
        setattr(model, key, value)
    return model


def _instantiate(candidate_cls: Any, kwargs_options: list[dict[str, Any]]) -> Any | None:
    """Try instantiating a class with multiple candidate kwargs."""
    if candidate_cls is None:
        return None
    for kwargs in kwargs_options:
        try:
            return candidate_cls(**kwargs)
        except TypeError:
            continue
        except Exception:
            continue
    return None


def _build_sliding_window_config(types_module: Any, trigger_tokens: int, target_tokens: int) -> Any | None:
    """Build a sliding-window config object when available in SDK."""
    for name in (
        "SlidingWindow",
        "SlidingWindowConfig",
        "ContextWindowSlidingWindow",
        "ContextWindowCompressionSlidingWindow",
    ):
        cls = getattr(types_module, name, None)
        if cls is None:
            continue
        obj = _instantiate(
            cls,
            [
                {"trigger_tokens": int(trigger_tokens), "target_tokens": int(target_tokens)},
                {"triggerTokens": int(trigger_tokens), "targetTokens": int(target_tokens)},
                {"max_tokens": int(target_tokens)},
                {},
            ],
        )
        if obj is not None:
            return obj
    return None


def build_context_compression_config(
    types_module: Any,
    *,
    trigger_tokens: int,
    target_tokens: int,
) -> Any | None:
    """Return SDK compression config object when supported; else None."""
    cfg_cls = getattr(types_module, "ContextWindowCompressionConfig", None)
    if cfg_cls is None:
        return None

    sliding_window = _build_sliding_window_config(types_module, trigger_tokens, target_tokens)
    options: list[dict[str, Any]] = [
        {
            "sliding_window": sliding_window,
            "trigger_tokens": int(trigger_tokens),
            "target_tokens": int(target_tokens),
        },
        {
            "slidingWindow": sliding_window,
            "triggerTokens": int(trigger_tokens),
            "targetTokens": int(target_tokens),
        },
        {"trigger_tokens": int(trigger_tokens), "target_tokens": int(target_tokens)},
        {"triggerTokens": int(trigger_tokens), "targetTokens": int(target_tokens)},
    ]
    if sliding_window is not None:
        options.extend(
            [
                {"sliding_window": sliding_window},
                {"slidingWindow": sliding_window},
            ]
        )
    return _instantiate(cfg_cls, options)



def _apply_optional_field(model: Any, field_names: tuple[str, ...], value: Any) -> tuple[Any, str | None]:
    """Try applying a value to the first supported field name."""
    if value is None:
        return model, None
    model_fields = _model_field_names(model)
    for field in field_names:
        if model_fields and field not in model_fields:
            continue
        try:
            updated = _copy_model_with_update(model, {field: value})
            return updated, field
        except Exception:
            continue
    return model, None


def build_live_run_config(
    base_run_config: Any,
    types_module: Any,
    *,
    compression_enabled: bool,
    compression_trigger_tokens: int,
    compression_target_tokens: int,
) -> tuple[Any, dict[str, Any]]:
    """
    Build a run config copy with optional compression.

    Returns:
      (run_config, metadata)
      metadata fields:
        - compression_enabled (bool)
        - compression_field (str|None)
    """
    run_config = _copy_model_with_update(base_run_config, {})
    meta: dict[str, Any] = {
        "compression_enabled": False,
        "compression_field": None,
    }

    if compression_enabled:
        compression_cfg = build_context_compression_config(
            types_module,
            trigger_tokens=max(1, int(compression_trigger_tokens)),
            target_tokens=max(1, int(compression_target_tokens)),
        )
        run_config, field = _apply_optional_field(
            run_config,
            (
                "context_window_compression",
                "contextWindowCompression",
                "context_window_compression_config",
            ),
            compression_cfg,
        )
        if field:
            meta["compression_enabled"] = True
            meta["compression_field"] = field

    return run_config, meta


def extract_total_token_estimate(event: Any) -> int | None:
    """Try extracting total token estimate from unknown event payload."""
    keys = (
        "total_tokens",
        "totalTokenCount",
        "token_count",
        "tokenCount",
        "usage_tokens",
        "usageTokenCount",
    )

    def _walk(value: Any, depth: int = 0) -> int | None:
        if depth > 4 or value is None:
            return None
        if isinstance(value, Mapping):
            for key in keys:
                if key in value:
                    try:
                        parsed = int(value[key])
                    except (TypeError, ValueError):
                        parsed = None
                    if parsed and parsed > 0:
                        return parsed
            for nested in value.values():
                found = _walk(nested, depth + 1)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for nested in value:
                found = _walk(nested, depth + 1)
                if found:
                    return found
        else:
            for attr in keys:
                if hasattr(value, attr):
                    try:
                        parsed = int(getattr(value, attr))
                    except (TypeError, ValueError):
                        parsed = None
                    if parsed and parsed > 0:
                        return parsed
            for dump_fn in ("model_dump", "dict", "to_dict"):
                fn = getattr(value, dump_fn, None)
                if callable(fn):
                    try:
                        dumped = fn()
                    except Exception:
                        continue
                    found = _walk(dumped, depth + 1)
                    if found:
                        return found
        return None

    return _walk(event)


def compute_retry_backoff(attempt: int, base_seconds: float) -> float:
    """Return exponential retry backoff with deterministic, bounded jitter."""
    safe_attempt = max(1, int(attempt))
    base = max(0.05, float(base_seconds))
    # deterministic pseudo-jitter from attempt number: +0.0 to +0.2s
    jitter = (safe_attempt % 5) * 0.05
    return min(6.0, (base * (2 ** (safe_attempt - 1))) + jitter)



