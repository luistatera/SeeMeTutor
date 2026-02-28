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
SUPPORTED_LANGS = frozenset({"en", "pt", "de"})

WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']+")
SPACES_RE = re.compile(r"\s+")

LANG_MARKERS: dict[str, set[str]] = {
    "en": {
        "the", "this", "that", "with", "what", "why", "how", "because", "are",
        "is", "you", "your", "can", "could", "would", "should", "understand",
        "practice",
    },
    "pt": {
        "nao", "não", "voce", "você", "porque", "como", "para", "com", "isso",
        "estou", "uma", "que", "de", "do", "da", "entendi", "explicar",
    },
    "de": {
        "ich", "nicht", "und", "ist", "der", "die", "das", "du", "wir", "fur",
        "für", "ein", "eine", "den", "dem", "bitte", "kann", "warum",
        "verstanden", "erklaren", "erklären",
    },
}

SPECIAL_DE_CHARS = set("äöüß")
SPECIAL_PT_CHARS = set("ãõáàâéêíóôúç")

CONFUSION_PATTERNS = [
    re.compile(
        r"\b(i\s*(?:do\s*not|don't)\s*(?:get|understand)|"
        r"i\s*(?:am|'m)\s*(?:still\s*)?confused|"
        r"i\s*(?:am|'m)\s*(?:still\s*)?lost|"
        r"not\s*sure|can\s*you\s*explain|what\s*does\s*that\s*mean)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ich\s*verstehe\s*(?:das\s*)?nicht|ich\s*bin\s*verwirrt|"
        r"keine\s*ahnung|ich\s*komme\s*nicht\s*mit|was\s*bedeutet\s*das)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(n[aã]o\s*entendi|nao\s*entendi|n[aã]o\s*percebi|nao\s*percebi|"
        r"estou\s*confus|pode\s*explicar|n[aã]o\s*sei|nao\s*sei|estou\s*perdid)\b",
        re.IGNORECASE,
    ),
]


def parse_int(value: Any, fallback: int, minimum: int = 1, maximum: int = 8) -> int:
    """Parse bounded integer with fallback."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def language_label(code: str) -> str:
    """Return display label for a language code."""
    normalized = str(code or "").strip().lower()
    if normalized.startswith("en"):
        return "English"
    if normalized.startswith("pt"):
        return "Portuguese"
    if normalized.startswith("de"):
        return "German"
    return code or "English"


def language_short(code: str) -> str:
    """Return short language key (en/pt/de) with English fallback."""
    normalized = str(code or "").strip().lower()
    if normalized.startswith("pt"):
        return "pt"
    if normalized.startswith("de"):
        return "de"
    return "en"


def normalize_preferred_language(value: str | None) -> str:
    """Normalize user preferred language to supported short keys."""
    normalized = str(value or "").strip().lower()
    if normalized.startswith("de"):
        return "de"
    if normalized.startswith("pt"):
        return "pt"
    if normalized.startswith("en"):
        return "en"
    return normalized or "en"


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
        "confusion_fallback": {
            "after_confusions": 2,
            "fallback_language": "l1",
            "fallback_turns": 2,
        },
    }


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

    mode = str(source.get("mode") or fallback.get("mode") or "auto").strip().lower()
    if mode not in SUPPORTED_LANGUAGE_MODES:
        mode = str(fallback.get("mode") or "auto")

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
        "max_l2_turns_before_recap": parse_int(
            source.get("max_l2_turns_before_recap"),
            parse_int(fallback.get("max_l2_turns_before_recap"), 3, minimum=1, maximum=6),
            minimum=1,
            maximum=6,
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
        },
    }
    return normalized


def build_language_contract(language_policy: dict[str, Any]) -> str:
    """Build a compact language contract string for system prompts."""
    mode = language_policy.get("mode", "auto")
    l1 = language_policy.get("l1", "en-US")
    l2 = language_policy.get("l2", "en-US")
    l1_label = language_label(l1)
    l2_label = language_label(l2)
    no_mix = bool(language_policy.get("no_mixed_language_same_turn", True))
    max_l2_turns = parse_int(
        language_policy.get("max_l2_turns_before_recap"),
        3,
        minimum=1,
        maximum=6,
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
                f"After at most {max_l2_turns} consecutive L2 practice turns, return to a short L1 recap.",
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
    if no_mix:
        contract_parts.append("Never mix two languages in the same tutor response.")
    contract_parts.append(
        f"If confusion appears {after_confusions} times in a row, force {fallback_label} for the next {fallback_turns} turns before retrying."
    )
    return " ".join(contract_parts)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(str(text or ""))]


def _lang_score_from_tokens(tokens: list[str], original_text: str) -> dict[str, float]:
    scores = {"en": 0.0, "pt": 0.0, "de": 0.0}
    for token in tokens:
        for lang, markers in LANG_MARKERS.items():
            if token in markers:
                scores[lang] += 1.0
    lowered = str(original_text or "").lower()
    if any(ch in lowered for ch in SPECIAL_DE_CHARS):
        scores["de"] += 1.5
    if any(ch in lowered for ch in SPECIAL_PT_CHARS):
        scores["pt"] += 1.5
    return scores


def detect_language(text: str) -> str:
    """Detect language of short transcript text using marker heuristics."""
    candidate = str(text or "").strip()
    if not candidate:
        return "unknown"

    tokens = _tokens(candidate)
    if not tokens:
        return "unknown"

    scores = _lang_score_from_tokens(tokens, candidate)
    best_lang = max(scores, key=scores.get)
    best_score = scores[best_lang]
    if best_score < 1.0:
        return "unknown"

    sorted_scores = sorted(scores.values(), reverse=True)
    if len(sorted_scores) >= 2 and (sorted_scores[0] - sorted_scores[1]) < 0.35:
        return "unknown"
    return best_lang


def analyze_turn_language(text: str) -> dict[str, Any]:
    """Analyze a full tutor turn for dominant language and mixed-language use."""
    clean = SPACES_RE.sub(" ", str(text or "")).strip()
    if not clean:
        return {
            "primary": "unknown",
            "mixed": False,
            "lang_set": [],
            "word_counts": {"en": 0, "pt": 0, "de": 0},
            "total_words": 0,
        }

    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+", clean) if p.strip()]
    if not pieces:
        pieces = [clean]

    lang_votes = {"en": 0, "pt": 0, "de": 0}
    word_counts = {"en": 0, "pt": 0, "de": 0}
    has_piece_level_mixing = False

    for piece in pieces:
        piece_tokens = _tokens(piece)
        piece_words = len(piece_tokens)
        if piece_words == 0:
            continue

        lang = detect_language(piece)
        if lang in SUPPORTED_LANGS:
            candidate_langs = {lang}
        else:
            marker_scores = _lang_score_from_tokens(piece_tokens, piece)
            candidate_langs = {
                code for code, score in marker_scores.items() if score >= 1.0
            }

        if len(candidate_langs) > 1:
            has_piece_level_mixing = True
            for candidate in candidate_langs:
                lang_votes[candidate] += 1
            continue

        if len(candidate_langs) == 1:
            only_lang = next(iter(candidate_langs))
            lang_votes[only_lang] += 1
            word_counts[only_lang] += piece_words

    lang_set = [lang for lang, count in lang_votes.items() if count > 0]
    mixed = has_piece_level_mixing or len(lang_set) > 1

    primary = "unknown"
    if lang_set:
        primary = max(lang_votes, key=lang_votes.get)

    total_words = sum(word_counts.values())
    return {
        "primary": primary,
        "mixed": mixed,
        "lang_set": lang_set,
        "word_counts": word_counts,
        "total_words": total_words,
    }


def is_confusion_signal(text: str) -> bool:
    """Return True when learner transcript indicates confusion."""
    candidate = str(text or "").strip()
    if not candidate:
        return False
    return any(pattern.search(candidate) for pattern in CONFUSION_PATTERNS)


def init_language_state(
    language_policy: dict | None = None,
    preferred_language: str | None = None,
) -> dict:
    """Return initial language runtime keys for session runtime_state."""
    policy = normalize_language_policy(language_policy, default_language_policy())
    preferred = normalize_preferred_language(preferred_language or policy.get("l1"))
    return {
        "language_policy": policy,
        "language_l1_short": language_short(policy.get("l1", "en-US")),
        "language_l2_short": language_short(policy.get("l2", "en-US")),
        "language_guided_phase": "explain",
        "language_force_language_key": None,
        "language_force_turns_remaining": 0,
        "language_l2_streak": 0,
        "language_last_student_lang": preferred if preferred in SUPPORTED_LANGS else "unknown",
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
    policy = runtime_state.get("language_policy", {})
    normalized = str(key or "").strip().lower()
    if normalized == "l1":
        return runtime_state.get("language_l1_short", "en")
    if normalized == "l2":
        return runtime_state.get("language_l2_short", "en")
    if normalized in SUPPORTED_LANGS:
        return normalized
    if policy.get("mode") == "auto":
        student_lang = runtime_state.get("language_last_student_lang", "unknown")
        if student_lang in SUPPORTED_LANGS:
            return student_lang
    return runtime_state.get("language_l1_short", "en")


def expected_language(runtime_state: dict) -> str:
    """Return expected tutor output language for next turn."""
    policy = runtime_state.get("language_policy", {})
    if runtime_state.get("language_force_turns_remaining", 0) > 0:
        return _resolve_language_key(
            runtime_state.get("language_force_language_key", "l1"),
            runtime_state,
        )

    mode = str(policy.get("mode") or "auto")
    if mode == "immersion":
        return runtime_state.get("language_l2_short", "en")
    if mode == "guided_bilingual":
        phase = runtime_state.get("language_guided_phase", "explain")
        key = (
            policy.get("explain_language", "l1")
            if phase == "explain"
            else policy.get("practice_language", "l2")
        )
        return _resolve_language_key(str(key), runtime_state)

    student_lang = runtime_state.get("language_last_student_lang", "unknown")
    if student_lang in SUPPORTED_LANGS:
        return student_lang
    return runtime_state.get("language_l1_short", "en")


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

    student_lang = detect_language(text)
    if student_lang in SUPPORTED_LANGS:
        runtime_state["language_last_student_lang"] = student_lang
    result["student_language"] = student_lang

    now = time.time()
    normalized = str(text or "").strip().lower()
    confusion = is_confusion_signal(text)

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
    if expected != runtime_state.get("language_last_announced_expected"):
        if result["control_prompt"] is None:
            result["control_prompt"] = _maybe_control_prompt(
                runtime_state,
                "student_language_update",
                force=False,
            )
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
    expected = expected_language(runtime_state)

    analysis = analyze_turn_language(turn_text)
    primary = analysis["primary"]
    mixed = bool(analysis["mixed"])

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
        primary in SUPPORTED_LANGS
        and last_tutor_lang in SUPPORTED_LANGS
        and primary != last_tutor_lang
    ):
        metrics["language_flips"] = int(metrics.get("language_flips", 0)) + 1
    if primary in SUPPORTED_LANGS:
        runtime_state["language_last_tutor_lang"] = primary

    pending_turn = metrics.get("fallback_pending_turn")
    target_lang = str(metrics.get("fallback_target_lang") or "")
    if pending_turn is not None and target_lang in SUPPORTED_LANGS and primary == target_lang:
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
    elif primary in SUPPORTED_LANGS:
        runtime_state["language_l2_streak"] = 0

    control_prompt = None
    events: list[dict[str, Any]] = []

    max_l2 = parse_int(
        policy.get("max_l2_turns_before_recap"),
        3,
        minimum=1,
        maximum=8,
    )
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
        runtime_state["language_guided_phase"] = (
            "practice"
            if runtime_state.get("language_guided_phase") == "explain"
            else "explain"
        )
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
