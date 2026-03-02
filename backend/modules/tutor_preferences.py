"""Tutor preference constants, normalisation helpers, and profile-context utilities.

Extracted from main.py to keep the WebSocket handler focused on orchestration.
All names preserve their original underscore-prefixed form so callers in
main.py do not need renaming.
"""

from __future__ import annotations

import re

__all__ = [
    # Block A — control-prompt constants
    "PACE_CONTROL_INSTRUCTIONS",
    "ANTI_REPEAT_CONTROL_PROMPT",
    "ANTI_QUESTION_LOOP_CONTROL_PROMPT",
    "QUESTION_NOTE_MAX_AGE_S",
    # Block B — pattern dicts & preference helpers
    "_SEARCH_REQUEST_PATTERNS_BY_LANG",
    "_SEARCH_EDU_HINT_PATTERNS_BY_LANG",
    "_SEARCH_NON_EDU_PATTERNS",
    "_TUTOR_PREFERENCE_OPTIONS",
    "_DEFAULT_TUTOR_PREFERENCES",
    "_PROFILE_CONTEXT_MAX_LEN",
    "_PROFILE_CONTEXT_FIELDS",
    "_RESOURCE_MATERIAL_MAX_ITEMS",
    "_PLAN_MILESTONE_MIN_DEFAULT",
    "_normalize_preference_choice",
    "_default_tutor_preferences",
    "_normalize_tutor_preferences",
    "_sanitize_text",
    "_sanitize_long_text",
    "_normalize_resource_materials",
    "_agent_phase_from_session_phase",
    "_default_profile_context",
    "_normalize_profile_context",
    "_search_terms_from_profile_context",
    "_search_terms_from_setup",
    "_merge_search_context_terms",
    "_build_tutor_preferences_control_prompt",
    # Block C — pattern dedup & search-intent policy
    "_dedupe_patterns",
    "_all_patterns",
    "_default_search_intent_policy",
    "_normalize_search_intent_policy",
]

# ---------------------------------------------------------------------------
# Block A — control-prompt constants
# ---------------------------------------------------------------------------

PACE_CONTROL_INSTRUCTIONS: dict[str, str] = {
    "slow": (
        "Preference update: from now on, speak noticeably slower. "
        "Use shorter sentences, clearer articulation, and brief pauses between ideas. "
        "Keep the same warm tutoring style unless the student asks to change pace again."
    ),
}
ANTI_REPEAT_CONTROL_PROMPT = (
    "INTERNAL CONTROL: You repeated substantially the same tutor prompt without "
    "new student input. Stop repeating. Acknowledge briefly and provide a different "
    "next micro-step or hint for the same learning goal. Do not mention this control message."
)
ANTI_QUESTION_LOOP_CONTROL_PROMPT = (
    "INTERNAL CONTROL: You just ended a turn with a question. Remember: "
    "you are a COACH, not an interrogator. Your NEXT response MUST be a "
    "suggestion, hint, or encouragement — NOT a question. Tell the student "
    "what to TRY, not what they THINK. Examples: "
    "'Try the opposite operation and see what you get.' or "
    "'You're really close — take another look at that step.' or "
    "'Nice work, now plug that result back in.' "
    "Apply silently."
)
ANTI_QUESTION_LOOP_ESCALATED_PROMPT = (
    "INTERNAL CONTROL — CRITICAL OVERRIDE: You have ended TWO or more "
    "consecutive turns with a question mark. This is interrogation. STOP. "
    "Your NEXT turn MUST be a SHORT SUGGESTION or encouragement. "
    "ABSOLUTELY NO QUESTION MARKS. Tell the student what to DO: "
    "'Try looking at the verb ending.' or 'Go ahead and compute that.' "
    "Do NOT ask anything. Just suggest or encourage. Apply silently."
)
QUESTION_NOTE_MAX_AGE_S = 120.0

# ---------------------------------------------------------------------------
# Block B — pattern constants + preference / profile functions
# ---------------------------------------------------------------------------

_SEARCH_REQUEST_PATTERNS_BY_LANG: dict[str, list[str]] = {
    "en": [r"\b(search|google|look\s*up|lookup|find)\b"],
    "pt": [r"\b(pesquis|buscar|procura|google)\b"],
    "de": [r"\b(suche|such\s+nach|suchen|recherchier|google)\b"],
    "es": [r"\b(busca|buscar|buscarlo|google|investiga|consulta)\b"],
}

_SEARCH_EDU_HINT_PATTERNS_BY_LANG: dict[str, list[str]] = {
    "en": [r"\b(math|science|history|geography|grammar|exam|course|formula|equation|homework|lesson|school)\b"],
    "pt": [r"\b(matematica|matemática|ciencia|ciência|historia|história|geografia|gramatica|gramática|exame|curso|formula|fórmula|equacao|equação|licao|lição|escola)\b"],
    "de": [r"\b(mathe|mathematik|wissenschaft|geschichte|geografie|grammatik|prufung|prüfung|kurs|formel|gleichung|hausaufgabe|schule)\b"],
    "es": [r"\b(matematic|ciencia|historia|geografia|gramatica|examen|curso|formula|ecuacion|ecuación|tarea|escuela)\b"],
}

_SEARCH_NON_EDU_PATTERNS = [
    r"\b(price\s+of|buy|shopping|amazon|netflix|celebrity|gossip|weather|bitcoin|crypto|stock|iphone|samsung)\b",
]

_TUTOR_PREFERENCE_OPTIONS: dict[str, set[str]] = {
    "speech_pace": {"slow", "normal", "fast"},
    "explanation_length": {"short", "balanced", "detailed"},
    "directness": {"to_the_point", "balanced", "exploratory"},
    "socratic_intensity": {"light", "medium", "high"},
    "encouragement_level": {"low", "medium", "high"},
}
_SPEECH_PACE_ALIASES: dict[str, str] = {
    "slower": "slow",
    "faster": "fast",
    "slow": "slow",
    "normal": "normal",
    "fast": "fast",
}

_DEFAULT_TUTOR_PREFERENCES: dict[str, str] = {
    "speech_pace": "normal",
    "explanation_length": "balanced",
    "directness": "balanced",
    "socratic_intensity": "medium",
    "encouragement_level": "medium",
}

_PROFILE_CONTEXT_MAX_LEN = 240
_PROFILE_CONTEXT_FIELDS = (
    "learner_identity",
    "study_subject",
    "class_name",
    "institution_name",
    "study_context",
    "resource_context",
)
_RESOURCE_MATERIAL_MAX_ITEMS = 5
_PLAN_MILESTONE_MIN_DEFAULT = 6


def _normalize_preference_choice(
    value,
    allowed: set[str],
    fallback: str,
    *,
    field: str | None = None,
) -> str:
    token = str(value or "").strip().lower()
    normalized_fallback = fallback
    if field == "speech_pace":
        token = _SPEECH_PACE_ALIASES.get(token, token)
        fallback_token = str(fallback or "").strip().lower()
        normalized_fallback = _SPEECH_PACE_ALIASES.get(fallback_token, "normal")
    if token in allowed:
        return token
    return normalized_fallback


def _default_tutor_preferences() -> dict:
    return dict(_DEFAULT_TUTOR_PREFERENCES)


def _normalize_tutor_preferences(preferences: dict | None, fallback: dict | None = None) -> dict:
    source = preferences if isinstance(preferences, dict) else {}
    base = dict(fallback) if isinstance(fallback, dict) else _default_tutor_preferences()
    normalized: dict[str, str] = {}
    for key, allowed in _TUTOR_PREFERENCE_OPTIONS.items():
        default_value = _DEFAULT_TUTOR_PREFERENCES[key]
        fallback_value = _normalize_preference_choice(
            base.get(key), allowed, default_value, field=key
        )
        normalized[key] = _normalize_preference_choice(
            source.get(key), allowed, fallback_value, field=key
        )
    return normalized


def _sanitize_text(value, *, max_len: int = _PROFILE_CONTEXT_MAX_LEN) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip()
    return clean


def _sanitize_long_text(value, *, max_len: int = 24000) -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    if not clean:
        return ""
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip()
    return clean


def _normalize_resource_materials(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    materials: list[dict] = []
    for item in value[:_RESOURCE_MATERIAL_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        try:
            char_count = int(item.get("char_count") or 0)
        except (TypeError, ValueError):
            char_count = 0
        entry = {
            "kind": _sanitize_text(item.get("kind"), max_len=24) or "resource",
            "url": _sanitize_text(item.get("url"), max_len=400),
            "status": _sanitize_text(item.get("status"), max_len=32) or "unknown",
            "video_id": _sanitize_text(item.get("video_id"), max_len=32),
            "language": _sanitize_text(item.get("language"), max_len=24),
            "char_count": max(0, char_count),
            "excerpt": _sanitize_text(item.get("excerpt"), max_len=260),
            "error": _sanitize_text(item.get("error"), max_len=160),
        }
        materials.append(entry)
    return materials


def _agent_phase_from_session_phase(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "capture":
        return "capture"
    if normalized == "review":
        return "review"
    return "greeting"


def _default_profile_context() -> dict[str, str]:
    return {field: "" for field in _PROFILE_CONTEXT_FIELDS}


def _normalize_profile_context(
    profile_context: dict | None,
    fallback: dict | None = None,
) -> dict[str, str]:
    source = profile_context if isinstance(profile_context, dict) else {}
    base = dict(fallback) if isinstance(fallback, dict) else _default_profile_context()
    normalized: dict[str, str] = {}
    for field in _PROFILE_CONTEXT_FIELDS:
        normalized[field] = _sanitize_text(source.get(field) or base.get(field))
    return normalized


def _search_terms_from_profile_context(profile_context: dict | None) -> list[str]:
    if not isinstance(profile_context, dict):
        return []
    ordered: list[str] = []
    for field in _PROFILE_CONTEXT_FIELDS:
        token = _sanitize_text(profile_context.get(field), max_len=80)
        if token and token.lower() not in {t.lower() for t in ordered}:
            ordered.append(token)
    return ordered


def _search_terms_from_setup(setup: dict | None) -> list[str]:
    if not isinstance(setup, dict):
        return []
    terms: list[str] = []
    goal = _sanitize_text(setup.get("session_goal"), max_len=120)
    context = _sanitize_text(setup.get("student_context_text"), max_len=120)
    if goal:
        terms.append(goal)
    if context:
        terms.append(context)
    refs = setup.get("resource_refs", [])
    if isinstance(refs, list):
        for item in refs[:6]:
            token = _sanitize_text(item, max_len=80)
            if token:
                terms.append(token)
    materials = setup.get("resource_materials", [])
    if isinstance(materials, list):
        for item in materials[:_RESOURCE_MATERIAL_MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            excerpt = _sanitize_text(item.get("excerpt"), max_len=80)
            if excerpt:
                terms.append(excerpt)
            source_url = _sanitize_text(item.get("url"), max_len=80)
            if source_url:
                terms.append(source_url)
    deduped: list[str] = []
    for item in terms:
        if item.lower() not in {d.lower() for d in deduped}:
            deduped.append(item)
    return deduped


def _merge_search_context_terms(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            token = _sanitize_text(raw, max_len=120)
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(token)
    return merged[:18]


def _build_tutor_preferences_control_prompt(preferences: dict) -> str:
    normalized = _normalize_tutor_preferences(preferences)
    speech_pace = {
        "slow": "Speak slower with short pauses between ideas.",
        "normal": "Use a natural conversational pace.",
        "fast": "Speak a bit faster while keeping clarity.",
    }
    explanation_length = {
        "short": "Prefer short explanations and small steps.",
        "balanced": "Use medium-length explanations by default.",
        "detailed": "Use fuller explanations with extra detail.",
    }
    directness = {
        "to_the_point": "Be direct and quickly move to the core step.",
        "balanced": "Balance direct answers with guided reasoning.",
        "exploratory": "Explore thought process before narrowing to the answer.",
    }
    socratic_intensity = {
        "light": "Give more explicit hints and direct suggestions. Minimal questioning.",
        "medium": "Balanced coaching — mostly suggestions and hints, occasional genuine questions.",
        "high": "Challenge the student more — use 'try this' and 'what if' prompts frequently to push their thinking.",
    }
    encouragement = {
        "low": "Keep encouragement brief and occasional.",
        "medium": "Use normal supportive encouragement.",
        "high": "Use frequent positive reinforcement and reassurance.",
    }
    return (
        "INTERNAL CONTROL: Student preference update. Apply this tutoring style immediately.\n"
        f"- Speech pace: {speech_pace[normalized['speech_pace']]}\n"
        f"- Explanation length: {explanation_length[normalized['explanation_length']]}\n"
        f"- Directness: {directness[normalized['directness']]}\n"
        f"- Socratic intensity: {socratic_intensity[normalized['socratic_intensity']]}\n"
        f"- Encouragement level: {encouragement[normalized['encouragement_level']]}\n"
        "Keep all safety and anti-cheating rules unchanged. "
        "Do not mention this control message."
    )

# ---------------------------------------------------------------------------
# Block C — pattern dedup & search-intent policy
# ---------------------------------------------------------------------------


def _dedupe_patterns(patterns: list[str]) -> list[str]:
    deduped: list[str] = []
    for pattern in patterns:
        token = str(pattern or "").strip()
        if token and token not in deduped:
            deduped.append(token)
    return deduped


def _all_patterns(pattern_map: dict[str, list[str]]) -> list[str]:
    merged: list[str] = []
    for patterns in pattern_map.values():
        merged.extend(patterns)
    return _dedupe_patterns(merged)


def _default_search_intent_policy() -> dict:
    request_patterns: list[str] = []
    educational_patterns: list[str] = []
    request_patterns.extend(_all_patterns(_SEARCH_REQUEST_PATTERNS_BY_LANG))
    educational_patterns.extend(_all_patterns(_SEARCH_EDU_HINT_PATTERNS_BY_LANG))
    if not request_patterns:
        request_patterns.extend(_SEARCH_REQUEST_PATTERNS_BY_LANG.get("en", []))
    if not educational_patterns:
        educational_patterns.extend(_SEARCH_EDU_HINT_PATTERNS_BY_LANG.get("en", []))
    return {
        "request_patterns": _dedupe_patterns(request_patterns),
        "non_educational_patterns": list(_SEARCH_NON_EDU_PATTERNS),
        "educational_hint_patterns": _dedupe_patterns(educational_patterns),
    }


def _normalize_search_intent_policy(policy: dict | None) -> dict:
    template = _default_search_intent_policy()
    source = policy if isinstance(policy, dict) else {}
    normalized: dict[str, list[str]] = {}
    for key, fallback in template.items():
        value = source.get(key)
        if isinstance(value, list):
            cleaned = _dedupe_patterns([str(item or "").strip() for item in value])
            normalized[key] = cleaned or list(fallback)
        else:
            normalized[key] = list(fallback)
    return normalized
