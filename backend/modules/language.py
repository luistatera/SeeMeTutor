"""
Multilingual runtime helpers.

Provides lightweight language detection and turn-level control hooks that can be
wired into the ADK stream:
- handle_student_transcript: detect learner language + confusion fallback
- finalize_tutor_turn: evaluate tutor turn language + guided/recap transitions

The module is intentionally framework-agnostic: it reads and mutates a shared
runtime_state dict and returns control prompts/events for the caller to send.
"""

from __future__ import annotations

import re
import time
from typing import Any


SUPPORTED_LANGUAGE_MODES = frozenset({"guided_bilingual", "immersion", "auto"})
RECAP_STRATEGIES = frozenset({"adaptive", "fixed"})

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
    """Parse bounded integer with fallback."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def language_label(code: str) -> str:
    """Return display label for a language code."""
    short = language_short(code)
    return LANGUAGE_DISPLAY_NAMES.get(short, code or "English")


def language_short(code: str) -> str:
    """Return short language key (IETF primary subtag) with English fallback."""
    normalized = str(code or "").strip().lower().replace("_", "-")
    if not normalized:
        return "en"
    match = re.match(r"^[a-z]{2,3}", normalized)
    if match:
        return match.group(0)
    return "en"


def normalize_preferred_language(value: str | None) -> str:
    """Normalize user preferred language to canonical lowercase BCP47-like format."""
    normalized = str(value or "").strip().lower().replace("_", "-")
    if not normalized:
        return "en"
    if re.match(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", normalized):
        return normalized
    return normalized


def default_language_policy() -> dict[str, Any]:
    """Default language policy used when profile policy is missing."""
    return {
        "policy_version": "v1",
        "mode": "auto",
        "l1": "en-US",
        "l2": "en-US",
        "explain_language": "l1",
        "practice_language": "l2",
        "no_mixed_language_same_turn": True,
        "max_l2_turns_before_recap": 3,
        "recap_policy": {
            "strategy": "adaptive",
            "base_l2_streak": 3,
            "min_l2_streak": 2,
            "max_l2_streak": 6,
        },
        "guided_phase_min_turns": 2,
        "detection_patterns": {},
        "confusion_fallback": {
            "after_confusions": 2,
            "fallback_language": "l1",
            "fallback_turns": 2,
            "signal_patterns": [],
        },
    }


def _normalize_pattern_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    patterns: list[str] = []
    for item in value:
        token = str(item or "").strip()
        if token:
            patterns.append(token)
    return patterns


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
    """Normalize language policy to a validated structure."""
    source = policy if isinstance(policy, dict) else {}
    fallback_confusion = (
        fallback.get("confusion_fallback", {})
        if isinstance(fallback.get("confusion_fallback"), dict)
        else {}
    )
    source_confusion = (
        source.get("confusion_fallback", {})
        if isinstance(source.get("confusion_fallback"), dict)
        else {}
    )
    fallback_recap = (
        fallback.get("recap_policy", {})
        if isinstance(fallback.get("recap_policy"), dict)
        else {}
    )
    source_recap = (
        source.get("recap_policy", {})
        if isinstance(source.get("recap_policy"), dict)
        else {}
    )

    mode = str(source.get("mode") or fallback.get("mode") or "auto").strip().lower()
    if mode not in SUPPORTED_LANGUAGE_MODES:
        mode = str(fallback.get("mode") or "auto")

    recap_strategy = str(
        source_recap.get("strategy") or fallback_recap.get("strategy") or "adaptive"
    ).strip().lower()
    if recap_strategy not in RECAP_STRATEGIES:
        recap_strategy = str(fallback_recap.get("strategy") or "adaptive")

    fallback_max_l2 = parse_int(
        fallback.get("max_l2_turns_before_recap"),
        3,
        minimum=1,
        maximum=12,
    )
    source_max_l2 = parse_int(
        source.get("max_l2_turns_before_recap"),
        fallback_max_l2,
        minimum=1,
        maximum=12,
    )

    normalized = {
        "policy_version": str(
            source.get("policy_version") or fallback.get("policy_version") or "v1"
        ),
        "mode": mode,
        "l1": str(source.get("l1") or fallback.get("l1") or "en-US"),
        "l2": str(source.get("l2") or fallback.get("l2") or "en-US"),
        "explain_language": str(
            source.get("explain_language") or fallback.get("explain_language") or "l1"
        ),
        "practice_language": str(
            source.get("practice_language") or fallback.get("practice_language") or "l2"
        ),
        "no_mixed_language_same_turn": bool(
            source.get("no_mixed_language_same_turn")
            if source.get("no_mixed_language_same_turn") is not None
            else fallback.get("no_mixed_language_same_turn", True)
        ),
        "max_l2_turns_before_recap": source_max_l2,
        "recap_policy": {
            "strategy": recap_strategy,
            "base_l2_streak": parse_int(
                source_recap.get("base_l2_streak"),
                parse_int(
                    fallback_recap.get("base_l2_streak"),
                    source_max_l2,
                    minimum=1,
                    maximum=12,
                ),
                minimum=1,
                maximum=12,
            ),
            "min_l2_streak": parse_int(
                source_recap.get("min_l2_streak"),
                parse_int(fallback_recap.get("min_l2_streak"), 2, minimum=1, maximum=10),
                minimum=1,
                maximum=10,
            ),
            "max_l2_streak": parse_int(
                source_recap.get("max_l2_streak"),
                parse_int(fallback_recap.get("max_l2_streak"), 6, minimum=1, maximum=12),
                minimum=1,
                maximum=12,
            ),
        },
        "guided_phase_min_turns": parse_int(
            source.get("guided_phase_min_turns"),
            parse_int(fallback.get("guided_phase_min_turns"), 2, minimum=1, maximum=4),
            minimum=1,
            maximum=4,
        ),
        "detection_patterns": (
            _normalize_pattern_map(source.get("detection_patterns"))
            or _normalize_pattern_map(fallback.get("detection_patterns"))
        ),
        "confusion_fallback": {
            "after_confusions": parse_int(
                source_confusion.get("after_confusions"),
                parse_int(fallback_confusion.get("after_confusions"), 2, minimum=1, maximum=5),
                minimum=1,
                maximum=5,
            ),
            "fallback_language": str(
                source_confusion.get("fallback_language")
                or fallback_confusion.get("fallback_language")
                or "l1"
            ),
            "fallback_turns": parse_int(
                source_confusion.get("fallback_turns"),
                parse_int(fallback_confusion.get("fallback_turns"), 2, minimum=1, maximum=6),
                minimum=1,
                maximum=6,
            ),
            "signal_patterns": (
                _normalize_pattern_list(source_confusion.get("signal_patterns"))
                or _normalize_pattern_list(fallback_confusion.get("signal_patterns"))
            ),
        },
    }
    recap_min = int(normalized["recap_policy"]["min_l2_streak"])
    recap_max = int(normalized["recap_policy"]["max_l2_streak"])
    if recap_max < recap_min:
        normalized["recap_policy"]["max_l2_streak"] = recap_min
    recap_base = int(normalized["recap_policy"]["base_l2_streak"])
    normalized["recap_policy"]["base_l2_streak"] = max(
        recap_min,
        min(int(normalized["recap_policy"]["max_l2_streak"]), recap_base),
    )
    return normalized


def _session_language_set(runtime_state: dict) -> set[str]:
    langs = set(runtime_state.get("language_session_langs") or [])
    l1 = language_short(runtime_state.get("language_l1_short") or "")
    l2 = language_short(runtime_state.get("language_l2_short") or "")
    if l1:
        langs.add(l1)
    if l2:
        langs.add(l2)
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


def resolve_recap_turn_limit(
    language_policy: dict[str, Any],
    runtime_state: dict | None = None,
) -> int:
    """Resolve L2 streak limit before recap. Adaptive by default."""
    recap_cfg = (
        language_policy.get("recap_policy", {})
        if isinstance(language_policy.get("recap_policy"), dict)
        else {}
    )
    strategy = str(recap_cfg.get("strategy") or "adaptive").strip().lower()
    if strategy not in RECAP_STRATEGIES:
        strategy = "adaptive"

    fixed_limit = parse_int(
        language_policy.get("max_l2_turns_before_recap"),
        3,
        minimum=1,
        maximum=12,
    )
    if strategy == "fixed":
        return parse_int(
            recap_cfg.get("base_l2_streak"),
            fixed_limit,
            minimum=1,
            maximum=12,
        )

    min_streak = parse_int(recap_cfg.get("min_l2_streak"), 2, minimum=1, maximum=10)
    max_streak = parse_int(recap_cfg.get("max_l2_streak"), 6, minimum=1, maximum=12)
    if max_streak < min_streak:
        max_streak = min_streak
    base_streak = parse_int(
        recap_cfg.get("base_l2_streak"),
        fixed_limit,
        minimum=min_streak,
        maximum=max_streak,
    )
    dynamic = base_streak

    if runtime_state:
        metrics = runtime_state.get("language_metrics", {})
        confusion_streak = int(runtime_state.get("language_confusion_streak", 0))
        tutor_turns = int(metrics.get("tutor_turns", 0))
        fallback_triggers = int(metrics.get("fallback_triggers", 0))
        guided_expected = int(metrics.get("guided_expected_turns", 0))
        guided_matched = int(metrics.get("guided_matched_turns", 0))

        if confusion_streak > 0:
            dynamic -= 1
        if tutor_turns > 0 and (fallback_triggers / max(1, tutor_turns)) >= 0.12:
            dynamic -= 1
        if guided_expected >= 4:
            adherence = guided_matched / max(1, guided_expected)
            if adherence >= 0.85:
                dynamic += 1
            elif adherence < 0.55:
                dynamic -= 1

    return max(min_streak, min(max_streak, dynamic))


def build_language_contract(language_policy: dict[str, Any]) -> str:
    """Build a compact language contract string for system prompts."""
    mode = language_policy.get("mode", "auto")
    l1 = language_policy.get("l1", "en-US")
    l2 = language_policy.get("l2", "en-US")
    l1_label = language_label(l1)
    l2_label = language_label(l2)
    no_mix = bool(language_policy.get("no_mixed_language_same_turn", True))
    max_l2_turns = resolve_recap_turn_limit(language_policy)
    recap_cfg = (
        language_policy.get("recap_policy", {})
        if isinstance(language_policy.get("recap_policy"), dict)
        else {}
    )
    recap_strategy = str(recap_cfg.get("strategy") or "adaptive").strip().lower()
    recap_min = parse_int(recap_cfg.get("min_l2_streak"), 2, minimum=1, maximum=10)
    recap_max = parse_int(recap_cfg.get("max_l2_streak"), 6, minimum=1, maximum=12)
    guided_phase_min_turns = parse_int(
        language_policy.get("guided_phase_min_turns"),
        2,
        minimum=1,
        maximum=4,
    )
    confusion = (
        language_policy.get("confusion_fallback", {})
        if isinstance(language_policy.get("confusion_fallback"), dict)
        else {}
    )
    after_confusions = parse_int(confusion.get("after_confusions"), 2, minimum=1, maximum=5)
    fallback_turns = parse_int(confusion.get("fallback_turns"), 2, minimum=1, maximum=6)
    fallback_key = str(confusion.get("fallback_language") or "l1").lower()
    fallback_label = l1_label if fallback_key == "l1" else l2_label

    contract_parts = [
        f"Mode: {mode}.",
        f"L1: {l1_label}.",
        f"L2: {l2_label}.",
    ]
    if mode == "guided_bilingual":
        contract_parts.extend(
            [
                f"Use {l1_label} for explanations and strategy coaching.",
                f"Use {l2_label} for practice drills and output exercises.",
                "When switching languages, say a short transition sentence first.",
                f"Stay in each guided phase for at least {guided_phase_min_turns} tutor turns before switching.",
            ]
        )
    elif mode == "immersion":
        contract_parts.extend(
            [
                f"Default to {l2_label} for almost all tutor turns.",
                f"Use {l1_label} only if the learner requests it or shows repeated confusion.",
            ]
        )
    else:
        contract_parts.extend(
            [
                "Follow the learner's active language preference naturally.",
                "If language preference is ambiguous, default to L1.",
            ]
        )
    if recap_strategy == "adaptive":
        contract_parts.append(
            f"Recap cadence is adaptive (target L2 streak between {recap_min} and {recap_max} turns based on confusion/progress)."
        )
    else:
        contract_parts.append(
            f"After at most {max_l2_turns} consecutive L2 practice turns, return to a short L1 recap."
        )
    if no_mix:
        contract_parts.append("Never mix two languages in the same tutor response.")
    contract_parts.append(
        f"If confusion appears {after_confusions} times in a row, force {fallback_label} for the next {fallback_turns} turns before retrying."
    )
    return " ".join(contract_parts)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(str(text or ""))]


def detect_language(
    text: str,
    *,
    candidate_langs: set[str] | None = None,
    runtime_state: dict | None = None,
) -> str:
    """Detect language from policy-defined intent patterns and session context."""
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
    if len(matched) > 1:
        return "unknown"

    return "unknown"


def analyze_turn_language(
    text: str,
    *,
    candidate_langs: set[str] | None = None,
    runtime_state: dict | None = None,
) -> dict[str, Any]:
    """Analyze a full tutor turn for dominant language and mixed-language use."""
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
            piece_langs = {lang}
        else:
            piece_langs = set()

        if len(piece_langs) > 1:
            has_piece_level_mixing = True
            for candidate in piece_langs:
                lang_votes[candidate] += 1
            continue

        if len(piece_langs) == 1:
            only_lang = next(iter(piece_langs))
            lang_votes[only_lang] += 1
            word_counts[only_lang] += piece_words
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


def is_confusion_signal(text: str, runtime_state: dict | None = None) -> bool:
    """Return True when learner transcript indicates confusion."""
    candidate = str(text or "").strip()
    if not candidate:
        return False

    if runtime_state:
        policy = runtime_state.get("language_policy", {})
        confusion_cfg = (
            policy.get("confusion_fallback", {})
            if isinstance(policy.get("confusion_fallback"), dict)
            else {}
        )
        signal_patterns = _normalize_pattern_list(confusion_cfg.get("signal_patterns"))
        if any(pattern.search(candidate) for pattern in _compiled_signal_patterns(signal_patterns)):
            return True

    token_count = len(_tokens(candidate))
    question_marks = candidate.count("?")
    if question_marks >= 2:
        return True
    if question_marks >= 1 and token_count <= 8:
        return True
    if token_count <= 3 and candidate.endswith("..."):
        return True
    return False


def init_language_state(
    language_policy: dict | None = None,
    preferred_language: str | None = None,
) -> dict:
    """Return initial language runtime keys for session runtime_state."""
    policy = normalize_language_policy(language_policy, default_language_policy())
    preferred = normalize_preferred_language(preferred_language or policy.get("l1"))
    l1_short = language_short(policy.get("l1", "en-US"))
    l2_short = language_short(policy.get("l2", "en-US"))
    session_langs = sorted({l1_short, l2_short})
    preferred_short = language_short(preferred)
    initial_student_lang = (
        preferred_short
        if preferred_short in session_langs
        else (l2_short if l2_short in session_langs else l1_short)
    )
    return {
        "language_policy": policy,
        "language_l1_short": l1_short,
        "language_l2_short": l2_short,
        "language_session_langs": session_langs,
        "language_guided_phase": "explain",
        "language_guided_phase_turns": 0,
        "language_force_language_key": None,
        "language_force_turns_remaining": 0,
        "language_l2_streak": 0,
        "language_last_student_lang": initial_student_lang,
        "language_last_tutor_lang": "unknown",
        "language_confusion_streak": 0,
        "language_confusion_grace_remaining": 0,
        "language_last_confusion_text": "",
        "language_last_confusion_at": 0.0,
        "language_last_control_signature": None,
        "language_last_announced_expected": None,
        "language_turn_text_parts": [],
        "language_turn_transcript_parts": [],
        "language_metrics": {
            "tutor_turns": 0,
            "single_language_turns": 0,
            "mixed_turns": 0,
            "guided_expected_turns": 0,
            "guided_matched_turns": 0,
            "fallback_triggers": 0,
            "fallback_latency_turns": [],
            "fallback_pending_turn": None,
            "fallback_target_lang": "",
            "confusion_signals": 0,
            "language_flips": 0,
            "l1_words": 0,
            "l2_words": 0,
            "recap_triggers": 0,
            "control_prompts_sent": 0,
        },
    }


def append_tutor_text_part(runtime_state: dict, text: str, *, source: str = "text") -> None:
    """Capture tutor text/transcript parts for end-of-turn language evaluation."""
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


def _resolve_language_key(key: str, runtime_state: dict) -> str:
    session_langs = _session_language_set(runtime_state)
    policy = runtime_state.get("language_policy", {})
    normalized = str(key or "").strip().lower()
    if normalized == "l1":
        return language_short(runtime_state.get("language_l1_short", "en"))
    if normalized == "l2":
        return language_short(runtime_state.get("language_l2_short", "en"))
    if normalized in session_langs:
        return normalized
    if policy.get("mode") == "auto":
        student_lang = runtime_state.get("language_last_student_lang", "unknown")
        if student_lang in session_langs:
            return student_lang
    return language_short(runtime_state.get("language_l1_short", "en"))


def expected_language(runtime_state: dict) -> str:
    """Return expected tutor output language for next turn."""
    policy = runtime_state.get("language_policy", {})
    session_langs = _session_language_set(runtime_state)
    if runtime_state.get("language_force_turns_remaining", 0) > 0:
        return _resolve_language_key(
            runtime_state.get("language_force_language_key", "l1"),
            runtime_state,
        )

    mode = str(policy.get("mode") or "auto")
    if mode == "immersion":
        return language_short(runtime_state.get("language_l2_short", "en"))
    if mode == "guided_bilingual":
        phase = runtime_state.get("language_guided_phase", "explain")
        key = (
            policy.get("explain_language", "l1")
            if phase == "explain"
            else policy.get("practice_language", "l2")
        )
        return _resolve_language_key(str(key), runtime_state)

    student_lang = runtime_state.get("language_last_student_lang", "unknown")
    if student_lang in session_langs:
        return student_lang
    return language_short(runtime_state.get("language_l1_short", "en"))


def build_internal_control(runtime_state: dict, reason: str) -> str:
    """Build hidden control prompt to steer next tutor response language."""
    policy = runtime_state.get("language_policy", {})
    mode = str(policy.get("mode") or "auto")
    expected = expected_language(runtime_state)

    l1_label = language_label(policy.get("l1", "en-US"))
    l2_label = language_label(policy.get("l2", "en-US"))
    expected_label = language_label(expected)

    parts = [
        "INTERNAL CONTROL: Language contract update.",
        f"Reason: {reason}.",
        f"Mode: {mode}.",
        f"L1={l1_label}, L2={l2_label}.",
        f"For the next tutor response, use {expected_label} only.",
        "Do not mix languages in one turn.",
        "Apply this silently and do not produce a standalone response to this control message.",
    ]

    if mode == "guided_bilingual":
        phase = runtime_state.get("language_guided_phase", "explain")
        phase_lang = l1_label if phase == "explain" else l2_label
        parts.append(
            f"Guided phase: {phase}. Respond entirely in {phase_lang}. Every word must be in {phase_lang}."
        )

    if runtime_state.get("language_force_turns_remaining", 0) > 0:
        parts.append(
            "Forced language lock active for "
            f"{runtime_state.get('language_force_turns_remaining', 0)} turns."
        )

    return " ".join(parts)


def _control_signature(runtime_state: dict) -> tuple[Any, ...]:
    return (
        expected_language(runtime_state),
        runtime_state.get("language_guided_phase"),
        runtime_state.get("language_force_language_key"),
        runtime_state.get("language_force_turns_remaining", 0),
        str(runtime_state.get("language_policy", {}).get("mode") or "auto"),
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
    """Update language runtime from student transcript and return prompt/events."""
    result: dict[str, Any] = {"control_prompt": None, "events": []}
    metrics = runtime_state.setdefault("language_metrics", {})
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
        policy = runtime_state.get("language_policy", {})
        if str(policy.get("mode") or "auto") == "guided_bilingual":
            l1_short = runtime_state.get("language_l1_short", "en")
            l2_short = runtime_state.get("language_l2_short", "en")
            if student_lang == l1_short:
                runtime_state["language_guided_phase"] = "explain"
                runtime_state["language_guided_phase_turns"] = 0
            elif student_lang == l2_short:
                runtime_state["language_guided_phase"] = "practice"
                runtime_state["language_guided_phase_turns"] = 0
    result["student_language"] = student_lang

    now = time.time()
    normalized = str(text or "").strip().lower()
    confusion = is_confusion_signal(text, runtime_state)

    if confusion:
        if (
            normalized == runtime_state.get("language_last_confusion_text", "")
            and (now - float(runtime_state.get("language_last_confusion_at", 0.0))) < 2.2
        ):
            return result

        runtime_state["language_last_confusion_text"] = normalized
        runtime_state["language_last_confusion_at"] = now
        runtime_state["language_confusion_streak"] = int(
            runtime_state.get("language_confusion_streak", 0)
        ) + 1
        runtime_state["language_confusion_grace_remaining"] = 3
        metrics["confusion_signals"] = int(metrics.get("confusion_signals", 0)) + 1

        result["events"].append(
            {
                "event": "confusion_signal",
                "streak": runtime_state["language_confusion_streak"],
                "lang": student_lang,
            }
        )

        policy = runtime_state.get("language_policy", {})
        confusion_cfg = (
            policy.get("confusion_fallback", {})
            if isinstance(policy.get("confusion_fallback"), dict)
            else {}
        )
        threshold = parse_int(confusion_cfg.get("after_confusions"), 2, minimum=1, maximum=5)
        fallback_turns = parse_int(confusion_cfg.get("fallback_turns"), 2, minimum=1, maximum=6)
        fallback_key = str(confusion_cfg.get("fallback_language") or "l1").lower()

        if (
            runtime_state.get("language_confusion_streak", 0) >= threshold
            and runtime_state.get("language_force_turns_remaining", 0) <= 0
        ):
            runtime_state["language_confusion_streak"] = 0
            runtime_state["language_force_language_key"] = fallback_key
            runtime_state["language_force_turns_remaining"] = fallback_turns
            runtime_state["language_guided_phase"] = "explain"
            runtime_state["language_guided_phase_turns"] = 0

            metrics["fallback_triggers"] = int(metrics.get("fallback_triggers", 0)) + 1
            metrics["fallback_pending_turn"] = int(metrics.get("tutor_turns", 0)) + 1
            metrics["fallback_target_lang"] = _resolve_language_key(fallback_key, runtime_state)

            result["control_prompt"] = _maybe_control_prompt(
                runtime_state,
                "confusion_fallback",
                force=True,
            )
            result["events"].append(
                {
                    "event": "fallback_triggered",
                    "count": metrics["fallback_triggers"],
                    "forced_lang": metrics["fallback_target_lang"],
                    "fallback_turns": fallback_turns,
                }
            )
    else:
        if runtime_state.get("language_confusion_streak", 0) > 0:
            runtime_state["language_confusion_grace_remaining"] = int(
                runtime_state.get("language_confusion_grace_remaining", 0)
            ) - 1
            if runtime_state.get("language_confusion_grace_remaining", 0) <= 0:
                runtime_state["language_confusion_streak"] = 0

    expected = expected_language(runtime_state)
    result["expected_language"] = expected
    runtime_state["language_last_announced_expected"] = expected

    return result


def finalize_tutor_turn(runtime_state: dict) -> dict[str, Any]:
    """Finalize turn language metrics and return follow-up control/events."""
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
    policy = runtime_state.get("language_policy", {})
    mode = str(policy.get("mode") or "auto")
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

    if mode == "guided_bilingual":
        metrics["guided_expected_turns"] = int(metrics.get("guided_expected_turns", 0)) + 1
        if (not mixed) and primary == expected:
            metrics["guided_matched_turns"] = int(metrics.get("guided_matched_turns", 0)) + 1

    l1_short = runtime_state.get("language_l1_short", "en")
    l2_short = runtime_state.get("language_l2_short", "en")
    metrics["l1_words"] = int(metrics.get("l1_words", 0)) + int(
        analysis["word_counts"].get(l1_short, 0)
    )
    metrics["l2_words"] = int(metrics.get("l2_words", 0)) + int(
        analysis["word_counts"].get(l2_short, 0)
    )

    last_tutor_lang = runtime_state.get("language_last_tutor_lang", "unknown")
    if (
        primary in session_langs
        and last_tutor_lang in session_langs
        and primary != last_tutor_lang
    ):
        metrics["language_flips"] = int(metrics.get("language_flips", 0)) + 1
    if primary in session_langs:
        runtime_state["language_last_tutor_lang"] = primary

    pending_turn = metrics.get("fallback_pending_turn")
    target_lang = str(metrics.get("fallback_target_lang") or "")
    if pending_turn is not None and target_lang in session_langs and primary == target_lang:
        delta_turns = int(metrics["tutor_turns"]) - int(pending_turn)
        latencies = metrics.setdefault("fallback_latency_turns", [])
        latencies.append(float(max(0, delta_turns)))
        metrics["fallback_pending_turn"] = None
        metrics["fallback_target_lang"] = ""

    if runtime_state.get("language_force_turns_remaining", 0) > 0:
        runtime_state["language_force_turns_remaining"] = int(
            runtime_state.get("language_force_turns_remaining", 0)
        ) - 1
        if runtime_state.get("language_force_turns_remaining", 0) <= 0:
            runtime_state["language_force_turns_remaining"] = 0
            runtime_state["language_force_language_key"] = None

    if primary == l2_short and not mixed:
        runtime_state["language_l2_streak"] = int(runtime_state.get("language_l2_streak", 0)) + 1
    elif primary in session_langs:
        runtime_state["language_l2_streak"] = 0

    control_prompt = None
    events: list[dict[str, Any]] = []

    max_l2 = resolve_recap_turn_limit(policy, runtime_state)
    if (
        mode in {"immersion", "guided_bilingual"}
        and runtime_state.get("language_l2_streak", 0) >= max_l2
        and runtime_state.get("language_force_turns_remaining", 0) == 0
    ):
        runtime_state["language_l2_streak"] = 0
        runtime_state["language_force_language_key"] = "l1"
        runtime_state["language_force_turns_remaining"] = 1
        metrics["recap_triggers"] = int(metrics.get("recap_triggers", 0)) + 1

        control_prompt = _maybe_control_prompt(
            runtime_state,
            "recap_after_l2_streak",
            force=True,
        )
        events.append(
            {
                "event": "recap_triggered",
                "count": metrics["recap_triggers"],
            }
        )

    if mode == "guided_bilingual" and runtime_state.get("language_force_turns_remaining", 0) == 0:
        phase_turns = int(runtime_state.get("language_guided_phase_turns", 0))
        if (not mixed) and primary == expected and primary in session_langs:
            phase_turns += 1
        else:
            phase_turns = 0
        runtime_state["language_guided_phase_turns"] = phase_turns

        guided_min_turns = parse_int(
            policy.get("guided_phase_min_turns"),
            2,
            minimum=1,
            maximum=4,
        )
        if phase_turns >= guided_min_turns:
            runtime_state["language_guided_phase"] = (
                "practice"
                if runtime_state.get("language_guided_phase") == "explain"
                else "explain"
            )
            runtime_state["language_guided_phase_turns"] = 0
            next_prompt = _maybe_control_prompt(
                runtime_state,
                "guided_phase_switch",
                force=True,
            )
            if control_prompt is None:
                control_prompt = next_prompt

    return {
        "control_prompt": control_prompt,
        "events": events,
        "analysis": analysis,
        "turn_text": turn_text,
        "expected_language": expected,
        "primary_language": primary,
        "mixed_language": mixed,
    }


def build_language_metric_snapshot(runtime_state: dict) -> dict[str, Any]:
    """Build language metrics snapshot suitable for UI/reporting."""
    metrics = runtime_state.get("language_metrics", {})
    tutor_turns = int(metrics.get("tutor_turns", 0))
    single_turns = int(metrics.get("single_language_turns", 0))
    guided_expected = int(metrics.get("guided_expected_turns", 0))
    guided_matched = int(metrics.get("guided_matched_turns", 0))

    purity_rate = (single_turns / tutor_turns) * 100 if tutor_turns else 0.0
    guided_rate = (guided_matched / guided_expected) * 100 if guided_expected else 0.0
    l1_words = int(metrics.get("l1_words", 0))
    l2_words = int(metrics.get("l2_words", 0))
    l1_l2_total = l1_words + l2_words
    l2_ratio = (l2_words / l1_l2_total) * 100 if l1_l2_total else 0.0

    return {
        "tutor_turns": tutor_turns,
        "purity_rate": round(purity_rate, 1),
        "mixed_turns": int(metrics.get("mixed_turns", 0)),
        "guided_adherence": round(guided_rate, 1),
        "guided_expected_turns": guided_expected,
        "guided_matched_turns": guided_matched,
        "fallback_triggers": int(metrics.get("fallback_triggers", 0)),
        "fallback_latency_turns": list(metrics.get("fallback_latency_turns", [])),
        "confusion_signals": int(metrics.get("confusion_signals", 0)),
        "language_flips": int(metrics.get("language_flips", 0)),
        "l1_words": l1_words,
        "l2_words": l2_words,
        "l2_ratio": round(l2_ratio, 1),
        "recap_triggers": int(metrics.get("recap_triggers", 0)),
        "control_prompts_sent": int(metrics.get("control_prompts_sent", 0)),
    }
