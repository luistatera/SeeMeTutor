"""
Multilingual runtime helpers — simplified for general study companion.

Provides lightweight language detection and turn-level analysis. The tutor
responds in whichever language the student speaks (auto mode only).

Key functions:
- detect_language: detect student's language from text
- analyze_turn_language: evaluate tutor turn for dominant language / mixing
- handle_student_transcript: update language state from student input
- finalize_tutor_turn: finalize turn metrics after tutor responds
- build_language_contract: simple contract string for system prompts
"""

from __future__ import annotations

import re
import time
from typing import Any


WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+")
SPACES_RE = re.compile(r"\s+")

LANGUAGE_DISPLAY_NAMES = {
    "en": "English",
    "pt": "Portuguese",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "nl": "Dutch",
}


def parse_int(value: Any, fallback: int, minimum: int = 1, maximum: int = 8) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def language_label(code: str) -> str:
    short = language_short(code)
    return LANGUAGE_DISPLAY_NAMES.get(short, code or "English")


def language_short(code: str) -> str:
    normalized = str(code or "").strip().lower().replace("_", "-")
    if not normalized:
        return "en"
    match = re.match(r"^[a-z]{2,3}", normalized)
    if match:
        return match.group(0)
    return "en"


def normalize_preferred_language(value: str | None) -> str:
    normalized = str(value or "").strip().lower().replace("_", "-")
    if not normalized:
        return "en"
    if re.match(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", normalized):
        return normalized
    return normalized


def default_language_policy() -> dict[str, Any]:
    return {
        "policy_version": "v2",
        "mode": "auto",
        "l1": "en-US",
        "no_mixed_language_same_turn": True,
        "detection_patterns": {},
    }


def _normalize_pattern_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _normalize_pattern_map(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, patterns in value.items():
        lang = language_short(str(key or ""))
        if not lang:
            continue
        cleaned = _normalize_pattern_list(patterns)
        if cleaned:
            normalized[lang] = cleaned
    return normalized


def normalize_language_policy(policy: dict | None, fallback: dict[str, Any]) -> dict[str, Any]:
    source = policy if isinstance(policy, dict) else {}
    return {
        "policy_version": str(
            source.get("policy_version") or fallback.get("policy_version") or "v2"
        ),
        "mode": "auto",
        "l1": str(source.get("l1") or fallback.get("l1") or "en-US"),
        "no_mixed_language_same_turn": bool(
            source.get("no_mixed_language_same_turn")
            if source.get("no_mixed_language_same_turn") is not None
            else fallback.get("no_mixed_language_same_turn", True)
        ),
        "detection_patterns": (
            _normalize_pattern_map(source.get("detection_patterns"))
            or _normalize_pattern_map(fallback.get("detection_patterns"))
        ),
    }


def _session_language_set(runtime_state: dict) -> set[str]:
    langs = set(runtime_state.get("language_session_langs") or [])
    l1 = language_short(runtime_state.get("language_l1_short") or "")
    if l1:
        langs.add(l1)
    return {lang for lang in langs if lang}


def _candidate_language_set(
    candidate_langs: set[str] | None,
    runtime_state: dict | None = None,
) -> set[str]:
    if not candidate_langs:
        if isinstance(runtime_state, dict):
            session_langs = _session_language_set(runtime_state)
            if session_langs:
                return session_langs
        return {"en"}
    normalized = {language_short(lang) for lang in candidate_langs if str(lang or "").strip()}
    return {lang for lang in normalized if lang}


def _compiled_signal_patterns(patterns: list[str]) -> list[re.Pattern]:
    compiled: list[re.Pattern] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def _language_detection_patterns(
    runtime_state: dict | None,
) -> dict[str, list[re.Pattern]]:
    if not isinstance(runtime_state, dict):
        return {}
    policy = runtime_state.get("language_policy", {})
    raw_patterns = (
        policy.get("detection_patterns", {})
        if isinstance(policy.get("detection_patterns"), dict)
        else {}
    )
    normalized = _normalize_pattern_map(raw_patterns)
    compiled: dict[str, list[re.Pattern]] = {}
    for lang, patterns in normalized.items():
        compiled_patterns = _compiled_signal_patterns(patterns)
        if compiled_patterns:
            compiled[lang] = compiled_patterns
    return compiled


def build_language_contract(language_policy: dict[str, Any]) -> str:
    l1 = language_policy.get("l1", "en-US")
    l1_label = language_label(l1)
    no_mix = bool(language_policy.get("no_mixed_language_same_turn", True))

    parts = [
        f"Mode: auto. Default language: {l1_label}.",
        "Respond in the same language the student uses.",
        "If the student's language is unclear, ask briefly which language they prefer.",
    ]
    if no_mix:
        parts.append("Never mix two languages in the same tutor response.")
    return " ".join(parts)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(str(text or ""))]


def detect_language(
    text: str,
    *,
    candidate_langs: set[str] | None = None,
    runtime_state: dict | None = None,
) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return "unknown"

    allowed = _candidate_language_set(candidate_langs, runtime_state)
    if len(allowed) == 1:
        return next(iter(allowed))

    detection_patterns = _language_detection_patterns(runtime_state)
    matched: set[str] = set()
    for lang in sorted(allowed):
        for pattern in detection_patterns.get(lang, []):
            if pattern.search(candidate):
                matched.add(lang)
                break

    if len(matched) == 1:
        return next(iter(matched))
    return "unknown"


def analyze_turn_language(
    text: str,
    *,
    candidate_langs: set[str] | None = None,
    runtime_state: dict | None = None,
) -> dict[str, Any]:
    allowed = _candidate_language_set(candidate_langs, runtime_state)
    ordered_allowed = sorted(allowed)

    clean = SPACES_RE.sub(" ", str(text or "")).strip()
    if not clean:
        return {
            "primary": "unknown",
            "mixed": False,
            "lang_set": [],
            "word_counts": {lang: 0 for lang in ordered_allowed},
            "total_words": 0,
        }

    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean) if p.strip()]
    if not pieces:
        pieces = [clean]

    lang_votes = {lang: 0 for lang in ordered_allowed}
    word_counts = {lang: 0 for lang in ordered_allowed}
    has_piece_level_mixing = False
    unresolved_words = 0
    total_words = 0

    for piece in pieces:
        piece_tokens = _tokens(piece)
        piece_words = len(piece_tokens)
        if piece_words == 0:
            continue
        total_words += piece_words

        lang = detect_language(piece, candidate_langs=allowed, runtime_state=runtime_state)
        if lang in allowed:
            lang_votes[lang] += 1
            word_counts[lang] += piece_words
        else:
            unresolved_words += piece_words

    lang_set = [lang for lang, count in lang_votes.items() if count > 0]
    mixed = has_piece_level_mixing or len(lang_set) > 1

    primary = "unknown"
    if lang_set:
        primary = max(lang_votes, key=lang_votes.get)
        if unresolved_words > 0:
            word_counts[primary] += unresolved_words
    elif unresolved_words > 0:
        fallback_lang = "unknown"
        if isinstance(runtime_state, dict):
            fallback_lang = expected_language(runtime_state)
        elif len(allowed) == 1:
            fallback_lang = next(iter(allowed))
        if fallback_lang in allowed:
            primary = fallback_lang
            lang_set = [fallback_lang]
            word_counts[fallback_lang] += unresolved_words

    return {
        "primary": primary,
        "mixed": mixed,
        "lang_set": lang_set,
        "word_counts": word_counts,
        "total_words": total_words,
    }



def init_language_state(
    language_policy: dict | None = None,
    preferred_language: str | None = None,
) -> dict:
    policy = normalize_language_policy(language_policy, default_language_policy())
    preferred = normalize_preferred_language(preferred_language or policy.get("l1"))
    l1_short = language_short(policy.get("l1", "en-US"))
    session_langs = sorted({l1_short})
    preferred_short = language_short(preferred)
    initial_student_lang = preferred_short if preferred_short else l1_short
    return {
        "language_policy": policy,
        "language_l1_short": l1_short,
        "language_session_langs": session_langs,
        "language_last_student_lang": initial_student_lang,
        "language_last_tutor_lang": "unknown",
        "language_last_control_signature": None,
        "language_turn_text_parts": [],
        "language_turn_transcript_parts": [],
        "language_metrics": {
            "tutor_turns": 0,
            "single_language_turns": 0,
            "mixed_turns": 0,
            "language_flips": 0,
            "control_prompts_sent": 0,
        },
    }


def append_tutor_text_part(runtime_state: dict, text: str, *, source: str = "text") -> None:
    clean = SPACES_RE.sub(" ", str(text or "")).strip()
    if not clean or clean.startswith("INTERNAL CONTROL:"):
        return

    if source == "transcript":
        parts = runtime_state.setdefault("language_turn_transcript_parts", [])
        if parts and parts[-1] == clean:
            return
        parts.append(clean)
        return

    runtime_state.setdefault("language_turn_text_parts", []).append(clean)


def expected_language(runtime_state: dict) -> str:
    session_langs = _session_language_set(runtime_state)
    student_lang = runtime_state.get("language_last_student_lang", "unknown")
    if student_lang in session_langs:
        return student_lang
    return language_short(runtime_state.get("language_l1_short", "en"))


def build_internal_control(runtime_state: dict, reason: str) -> str:
    expected = expected_language(runtime_state)
    expected_label = language_label(expected)

    parts = [
        "INTERNAL CONTROL: Language update.",
        f"Reason: {reason}.",
        f"For the next tutor response, use {expected_label} only.",
        "Do not mix languages in one turn.",
        "Apply this silently and do not produce a standalone response to this control message.",
    ]
    return " ".join(parts)


def _control_signature(runtime_state: dict) -> tuple[Any, ...]:
    return (
        expected_language(runtime_state),
        "auto",
    )


def _maybe_control_prompt(runtime_state: dict, reason: str, *, force: bool) -> str | None:
    signature = _control_signature(runtime_state)
    if (not force) and signature == runtime_state.get("language_last_control_signature"):
        return None
    runtime_state["language_last_control_signature"] = signature
    metrics = runtime_state.setdefault("language_metrics", {})
    metrics["control_prompts_sent"] = int(metrics.get("control_prompts_sent", 0)) + 1
    return build_internal_control(runtime_state, reason)


def handle_student_transcript(text: str, runtime_state: dict) -> dict[str, Any]:
    result: dict[str, Any] = {"control_prompt": None, "events": []}
    session_langs = _session_language_set(runtime_state)

    student_lang = detect_language(
        text,
        candidate_langs=session_langs,
        runtime_state=runtime_state,
    )
    if student_lang == "unknown":
        previous = str(runtime_state.get("language_last_student_lang") or "unknown")
        if previous in session_langs:
            student_lang = previous

    if student_lang in session_langs:
        runtime_state["language_last_student_lang"] = student_lang
    result["student_language"] = student_lang

    expected = expected_language(runtime_state)
    result["expected_language"] = expected

    return result


def finalize_tutor_turn(runtime_state: dict) -> dict[str, Any]:
    transcript_parts = [
        p
        for p in runtime_state.get("language_turn_transcript_parts", [])
        if not str(p).startswith("INTERNAL CONTROL:")
    ]
    text_parts = [
        p
        for p in runtime_state.get("language_turn_text_parts", [])
        if not str(p).startswith("INTERNAL CONTROL:")
    ]

    transcript_text = " ".join(transcript_parts).strip()
    text_only = " ".join(text_parts).strip()
    turn_text = transcript_text or text_only

    runtime_state["language_turn_transcript_parts"] = []
    runtime_state["language_turn_text_parts"] = []

    if not turn_text:
        return {"control_prompt": None, "events": [], "analysis": None, "turn_text": ""}

    metrics = runtime_state.setdefault("language_metrics", {})
    session_langs = _session_language_set(runtime_state)
    expected = expected_language(runtime_state)

    analysis = analyze_turn_language(
        turn_text,
        candidate_langs=session_langs,
        runtime_state=runtime_state,
    )
    primary = analysis["primary"]
    mixed = bool(analysis["mixed"])
    if primary == "unknown" and (not mixed) and expected in session_langs:
        primary = expected
        analysis["primary"] = expected
        if not analysis["lang_set"]:
            analysis["lang_set"] = [expected]
        if analysis["word_counts"].get(expected, 0) <= 0:
            analysis["word_counts"][expected] = int(analysis.get("total_words", 0))

    metrics["tutor_turns"] = int(metrics.get("tutor_turns", 0)) + 1
    if mixed:
        metrics["mixed_turns"] = int(metrics.get("mixed_turns", 0)) + 1
    else:
        metrics["single_language_turns"] = int(metrics.get("single_language_turns", 0)) + 1

    last_tutor_lang = runtime_state.get("language_last_tutor_lang", "unknown")
    if (
        primary in session_langs
        and last_tutor_lang in session_langs
        and primary != last_tutor_lang
    ):
        metrics["language_flips"] = int(metrics.get("language_flips", 0)) + 1
    if primary in session_langs:
        runtime_state["language_last_tutor_lang"] = primary

    return {
        "control_prompt": None,
        "events": [],
        "analysis": analysis,
        "turn_text": turn_text,
        "expected_language": expected,
        "primary_language": primary,
        "mixed_language": mixed,
    }


def build_language_metric_snapshot(runtime_state: dict) -> dict[str, Any]:
    metrics = runtime_state.get("language_metrics", {})
    tutor_turns = int(metrics.get("tutor_turns", 0))
    single_turns = int(metrics.get("single_language_turns", 0))

    purity_rate = (single_turns / tutor_turns) * 100 if tutor_turns else 0.0

    return {
        "tutor_turns": tutor_turns,
        "purity_rate": round(purity_rate, 1),
        "mixed_turns": int(metrics.get("mixed_turns", 0)),
        "language_flips": int(metrics.get("language_flips", 0)),
        "control_prompts_sent": int(metrics.get("control_prompts_sent", 0)),
    }
