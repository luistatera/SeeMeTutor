"""
Search-intent helpers for educational grounding requests.
"""

from __future__ import annotations

import re
import time
from typing import Any


DEFAULT_SEARCH_POLICY = {
    # Keep defaults empty so behavior is policy-driven rather than hardcoded.
    "request_patterns": [],
    "non_educational_patterns": [],
    "educational_hint_patterns": [],
}
SEARCH_INTENT_SIGNAL_WINDOW_S = 12.0


def _compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    compiled: list[re.Pattern] = []
    for pattern in patterns:
        candidate = str(pattern or "").strip()
        if not candidate:
            continue
        try:
            compiled.append(re.compile(candidate, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _resolve_policy(runtime_state: dict[str, Any] | None) -> dict[str, list[str]]:
    if not isinstance(runtime_state, dict):
        return {
            key: list(value)
            for key, value in DEFAULT_SEARCH_POLICY.items()
        }
    raw_policy = runtime_state.get("search_intent_policy", {})
    if not isinstance(raw_policy, dict):
        raw_policy = {}
    resolved: dict[str, list[str]] = {}
    for key, fallback in DEFAULT_SEARCH_POLICY.items():
        raw_value = raw_policy.get(key)
        if isinstance(raw_value, list):
            cleaned = [str(item or "").strip() for item in raw_value if str(item or "").strip()]
            resolved[key] = cleaned or list(fallback)
        else:
            resolved[key] = list(fallback)
    return resolved


def detect_explicit_search_request(text: str, runtime_state: dict[str, Any] | None = None) -> bool:
    candidate = str(text or "").strip()
    if not candidate:
        return False

    if isinstance(runtime_state, dict):
        until = float(runtime_state.get("search_intent_signal_until", 0.0))
        if until > time.time():
            return True

    policy = _resolve_policy(runtime_state)
    return any(
        pattern.search(candidate)
        for pattern in _compile_patterns(policy.get("request_patterns", []))
    )


def extract_search_query(text: str) -> str:
    candidate = re.sub(r"\s+", " ", str(text or "")).strip()
    return candidate


def is_likely_educational_search(text: str, runtime_state: dict[str, Any]) -> bool:
    candidate = str(text or "")
    policy = _resolve_policy(runtime_state)
    non_edu_patterns = _compile_patterns(policy.get("non_educational_patterns", []))
    edu_hint_patterns = _compile_patterns(policy.get("educational_hint_patterns", []))

    if any(pattern.search(candidate) for pattern in non_edu_patterns):
        return False
    if any(pattern.search(candidate) for pattern in edu_hint_patterns):
        return True
    topic_title = str(runtime_state.get("topic_title") or "")
    track_title = str(runtime_state.get("track_title") or "")
    context_terms = runtime_state.get("search_context_terms", [])
    if topic_title and topic_title.lower() in candidate.lower():
        return True
    if track_title and track_title.lower() in candidate.lower():
        return True
    if isinstance(context_terms, list):
        lowered = candidate.lower()
        for term in context_terms:
            token = str(term or "").strip().lower()
            if len(token) >= 3 and token in lowered:
                return True
    return False


def build_force_search_control_prompt(query: str) -> str:
    return (
        "INTERNAL CONTROL: The student explicitly requested an educational web search. "
        f"Immediately call google_search with query: \"{query}\" before answering. "
        "Then give a concise answer and include at least one source URL in the response. "
        "Apply silently and do not produce a standalone response to this control message."
    )
