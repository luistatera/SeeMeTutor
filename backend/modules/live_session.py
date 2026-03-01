"""
Live session helpers for compression and session resumption.

This module keeps SDK-specific logic out of main.py:
- build RunConfig with optional context compression + resumption handle
- extract resumption handles from runner events
- persist/load latest resumption handle in Firestore
"""

from __future__ import annotations

import inspect
import logging
import time
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


def build_session_resumption_config(types_module: Any, handle: str) -> Any | None:
    """Return SDK resumption config object when supported; else None."""
    clean_handle = str(handle or "").strip()
    if not clean_handle:
        return None
    for name in (
        "SessionResumptionConfig",
        "SessionResumption",
        "LiveSessionResumptionConfig",
    ):
        cls = getattr(types_module, name, None)
        if cls is None:
            continue
        obj = _instantiate(
            cls,
            [
                {"handle": clean_handle},
                {"session_handle": clean_handle},
                {"resume_handle": clean_handle},
                {"token": clean_handle},
            ],
        )
        if obj is not None:
            return obj
    return None


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
    resumption_handle: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """
    Build a run config copy with optional compression and resumption.

    Returns:
      (run_config, metadata)
      metadata fields:
        - compression_enabled (bool)
        - compression_field (str|None)
        - resumption_enabled (bool)
        - resumption_field (str|None)
    """
    run_config = _copy_model_with_update(base_run_config, {})
    meta: dict[str, Any] = {
        "compression_enabled": False,
        "compression_field": None,
        "resumption_enabled": False,
        "resumption_field": None,
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

    if resumption_handle:
        resum_cfg = build_session_resumption_config(types_module, resumption_handle)
        if resum_cfg is None:
            resum_cfg = {"handle": str(resumption_handle)}
        run_config, field = _apply_optional_field(
            run_config,
            (
                "session_resumption",
                "sessionResumption",
                "session_resumption_config",
            ),
            resum_cfg,
        )
        if field:
            meta["resumption_enabled"] = True
            meta["resumption_field"] = field

    return run_config, meta


def _extract_handle(value: Any, *, depth: int = 0) -> str | None:
    """Recursively extract a plausible handle string from unknown payloads."""
    if depth > 4 or value is None:
        return None
    if isinstance(value, str):
        token = value.strip()
        if len(token) >= 12 and " " not in token:
            return token
        return None
    if isinstance(value, Mapping):
        for key in (
            "handle",
            "session_handle",
            "resume_handle",
            "token",
            "sessionResumptionHandle",
            "session_resumption_handle",
        ):
            nested = value.get(key)
            found = _extract_handle(nested, depth=depth + 1)
            if found:
                return found
        for nested in value.values():
            found = _extract_handle(nested, depth=depth + 1)
            if found:
                return found
        return None

    for attr in (
        "handle",
        "session_handle",
        "resume_handle",
        "token",
        "session_resumption_handle",
        "sessionResumptionHandle",
    ):
        if hasattr(value, attr):
            found = _extract_handle(getattr(value, attr), depth=depth + 1)
            if found:
                return found

    for dump_fn in ("model_dump", "dict", "to_dict"):
        fn = getattr(value, dump_fn, None)
        if callable(fn):
            try:
                dumped = fn()
            except Exception:
                continue
            found = _extract_handle(dumped, depth=depth + 1)
            if found:
                return found
    return None


def extract_session_resumption_handle(event: Any) -> str | None:
    """Extract session resumption handle from runner event payload."""
    for attr in (
        "session_resumption",
        "session_resumption_update",
        "sessionResumption",
        "sessionResumptionUpdate",
        "session",
    ):
        if hasattr(event, attr):
            found = _extract_handle(getattr(event, attr))
            if found:
                return found

    for dump_fn in ("model_dump", "dict", "to_dict"):
        fn = getattr(event, dump_fn, None)
        if not callable(fn):
            continue
        try:
            payload = fn()
        except Exception:
            continue
        found = _extract_handle(payload)
        if found:
            return found
    return None


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


async def save_resumption_handle(
    firestore_client: Any,
    *,
    student_id: str,
    session_id: str,
    handle: str,
    ttl_seconds: int = 24 * 60 * 60,
) -> bool:
    """Persist latest resumption handle for a student."""
    if not firestore_client:
        return False
    sid = str(student_id or "").strip().lower()
    token = str(handle or "").strip()
    if not sid or not token:
        return False
    now = time.time()
    doc = {
        "student_id": sid,
        "session_id": str(session_id or ""),
        "handle": token,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + max(60, int(ttl_seconds)),
    }
    try:
        await firestore_client.collection("session_resumption").document(sid).set(doc, merge=True)
        return True
    except Exception:
        logger.warning("Failed to persist session resumption handle", exc_info=True)
        return False


async def load_latest_resumption_handle(
    firestore_client: Any,
    *,
    student_id: str,
    now_ts: float | None = None,
) -> dict[str, Any] | None:
    """Load latest non-expired resumption handle for a student."""
    if not firestore_client:
        return None
    sid = str(student_id or "").strip().lower()
    if not sid:
        return None
    now = float(now_ts or time.time())
    try:
        snap = await firestore_client.collection("session_resumption").document(sid).get()
    except Exception:
        logger.warning("Failed to read session resumption handle", exc_info=True)
        return None
    if not snap.exists:
        return None
    payload = snap.to_dict() or {}
    expires_at = float(payload.get("expires_at") or 0.0)
    handle = str(payload.get("handle") or "").strip()
    if not handle or expires_at <= now:
        return None
    return {
        "student_id": sid,
        "session_id": str(payload.get("session_id") or ""),
        "handle": handle,
        "expires_at": expires_at,
        "updated_at": float(payload.get("updated_at") or 0.0),
    }


def compute_retry_backoff(attempt: int, base_seconds: float) -> float:
    """Return exponential retry backoff with deterministic, bounded jitter."""
    safe_attempt = max(1, int(attempt))
    base = max(0.05, float(base_seconds))
    # deterministic pseudo-jitter from attempt number: +0.0 to +0.2s
    jitter = (safe_attempt % 5) * 0.05
    return min(6.0, (base * (2 ** (safe_attempt - 1))) + jitter)


def describe_type_signature(obj: Any) -> str:
    """Debug helper for capability logs."""
    try:
        sig = inspect.signature(obj)
        return str(sig)
    except Exception:
        return "<unknown>"

