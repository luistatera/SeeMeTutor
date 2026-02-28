"""
SeeMe Tutor — FastAPI backend.

Bridges browser WebSocket connections to the Gemini Live API.
Audio and video frames flow from the browser to Gemini; audio responses
and text transcripts flow back to the browser.
"""

import asyncio
import audioop
import base64
import binascii
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

from agent import tutor_agent, SYSTEM_PROMPT
from queues import (
    register_whiteboard_queue,
    unregister_whiteboard_queue,
    register_topic_update_queue,
    unregister_topic_update_queue,
)
from test_report import create_report, remove_report
from modules.proactive import (
    proactive_idle_orchestrator,
    init_proactive_state,
    reset_silence_tracking,
    sanitize_tutor_output,
)
from modules.screen_share import (
    init_screen_share_state,
    get_switch_prompt,
    STOP_SHARING_PROMPT,
    SOURCE_SWITCH_COOLDOWN_S,
)
from modules.whiteboard import (
    init_whiteboard_state,
    whiteboard_dispatcher,
)
from modules.guardrails import (
    init_guardrails_state,
    check_student_input,
    check_tutor_output,
    select_reinforcement,
    record_guardrail_event,
    record_reinforcement,
)
from modules.grounding import (
    init_grounding_state,
    extract_grounding,
    extract_inline_url_citations,
)
from modules.language import (
    init_language_state,
    append_tutor_text_part,
    handle_student_transcript as handle_language_student_transcript,
    finalize_tutor_turn as finalize_language_tutor_turn,
    build_language_metric_snapshot,
    language_label as module_language_label,
    language_short as module_language_short,
    normalize_preferred_language as module_normalize_preferred_language,
    default_language_policy as module_default_language_policy,
    normalize_language_policy as module_normalize_language_policy,
    build_language_contract as module_build_language_contract,
)
from modules.latency import (
    init_latency_state,
    record_latency_metric,
    build_latency_report,
    format_latency_summary,
)
from modules.security import (
    SlidingWindowRateLimiter,
    build_security_headers,
    extract_client_ip,
    parse_allowed_origins,
)
from modules.conversation import expects_student_reply, is_near_duplicate
from modules.conversation import is_question_like_turn
from modules.search_intent import (
    build_force_search_control_prompt,
    detect_explicit_search_request,
    extract_search_query,
    is_likely_educational_search,
    SEARCH_INTENT_SIGNAL_WINDOW_S,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Session debug log — writes to backend/debug.log for diagnosing silence etc.
# ---------------------------------------------------------------------------
from logging.handlers import RotatingFileHandler

_debug_logger = logging.getLogger("session_debug")
_debug_logger.setLevel(logging.DEBUG)
_debug_logger.propagate = False
_debug_fh = RotatingFileHandler(
    BASE_DIR / "debug.log", maxBytes=5 * 1024 * 1024, backupCount=2
)
_debug_fh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%H:%M:%S"))
_debug_logger.addHandler(_debug_fh)

# ---------------------------------------------------------------------------
# Gemini backend: Vertex AI
# ---------------------------------------------------------------------------
DEMO_ACCESS_CODE = os.environ.get("DEMO_ACCESS_CODE", "")

# Production (Cloud Run) / local dev with `gcloud auth application-default login`
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT_ID", "seeme-tutor"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_REGION", "europe-west1"))
logger.info(
    "Gemini backend: Vertex AI (project=%s, location=%s)",
    os.environ["GOOGLE_CLOUD_PROJECT"],
    os.environ["GOOGLE_CLOUD_LOCATION"],
)


SESSION_TIMEOUT_SECONDS = 20 * 60  # 20-minute focused session limit
IDLE_CHECKIN_1_SECONDS = 10
IDLE_CHECKIN_2_SECONDS = 25
IDLE_AUTO_AWAY_SECONDS = 90
MIC_KICKOFF_SECONDS = 5
ADK_STREAM_MAX_RETRIES = 1
ADK_STREAM_RETRY_BACKOFF_S = 0.6
TURN_TO_TURN_MAX_GAP_MS = 3000
RESPONSE_REF_MAX_AGE_MS = 2500
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
    "INTERNAL CONTROL: Recent tutor turns were too question-heavy. "
    "Next response must start with one short declarative hint/explanation, "
    "then at most one question. Do not ask multiple consecutive questions. "
    "Apply silently and do not produce a standalone response to this control message."
)

# Per-session latency tracking: session_id -> {"last_audio_in": float, "awaiting_first_response": bool}
_latency_state: dict[str, dict] = {}
# Per-session debug counters: session_id -> {audio_in: int, audio_out: int, ...}
_debug_counters: dict[str, dict] = {}
_active_student_sessions: dict[str, dict] = {}
_active_student_sessions_lock = asyncio.Lock()

_STUDENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 10000) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


_DEFAULT_CORS_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]
CORS_ALLOWED_ORIGINS = parse_allowed_origins(
    os.environ.get("CORS_ALLOWED_ORIGINS"),
    defaults=_DEFAULT_CORS_ORIGINS,
)
CORS_ALLOW_CREDENTIALS = _env_bool("CORS_ALLOW_CREDENTIALS", True)
if "*" in CORS_ALLOWED_ORIGINS and CORS_ALLOW_CREDENTIALS:
    logger.warning("CORS allow_credentials disabled because allow_origins includes '*'.")
    CORS_ALLOW_CREDENTIALS = False

SECURITY_CSP_ENABLED = _env_bool("SECURITY_CSP_ENABLED", True)
SECURITY_HEADERS = build_security_headers(csp_enabled=SECURITY_CSP_ENABLED)

HTTP_RATE_LIMIT_MAX = _env_int("HTTP_RATE_LIMIT_MAX", 120, minimum=10, maximum=10000)
HTTP_RATE_LIMIT_WINDOW_S = _env_int("HTTP_RATE_LIMIT_WINDOW_S", 60, minimum=1, maximum=3600)
WS_CONNECT_RATE_LIMIT_MAX = _env_int("WS_CONNECT_RATE_LIMIT_MAX", 20, minimum=1, maximum=1000)
WS_CONNECT_RATE_LIMIT_WINDOW_S = _env_int("WS_CONNECT_RATE_LIMIT_WINDOW_S", 60, minimum=1, maximum=3600)

_http_rate_limiter = SlidingWindowRateLimiter()
_ws_rate_limiter = SlidingWindowRateLimiter()


def _anonymize_ip(ip: str) -> str:
    """Hash an IP address for Firestore storage (never persist raw IPs)."""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _language_label(code: str) -> str:
    return module_language_label(code)


def _parse_int(value, fallback: int, minimum: int = 1, maximum: int = 8) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _safe_order_index(value, fallback: int = 9999) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_preferred_language(value: str | None) -> str:
    return module_normalize_preferred_language(value)


def _default_language_policy() -> dict:
    return module_default_language_policy()


def _normalize_language_policy(policy: dict | None, fallback: dict) -> dict:
    return module_normalize_language_policy(policy, fallback)


def _build_language_contract(language_policy: dict) -> str:
    return module_build_language_contract(language_policy)


_LANGUAGE_DETECTION_PATTERNS: dict[str, list[str]] = {
    "en": [
        r"\b(what|why|how|can you|please|answer|explain|help)\b",
    ],
    "pt": [
        r"\b(nao|não|porque|como|pode|explicar|ajuda|entendi)\b",
    ],
    "de": [
        r"\b(ich|nicht|warum|wie|kannst|bitte|verstehe|erklar|erklär)\b",
    ],
    "es": [
        r"\b(que|qué|como|cómo|puedes|explica|ayuda|entiendo|por que|por qué)\b",
    ],
}

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


def _policy_language_codes(language_policy: dict) -> list[str]:
    l1 = module_language_short(language_policy.get("l1", "en-US"))
    l2 = module_language_short(language_policy.get("l2", "en-US"))
    ordered: list[str] = []
    for lang in (l1, l2):
        if lang and lang not in ordered:
            ordered.append(lang)
    if not ordered:
        ordered.append("en")
    return ordered


def _dedupe_patterns(patterns: list[str]) -> list[str]:
    deduped: list[str] = []
    for pattern in patterns:
        token = str(pattern or "").strip()
        if token and token not in deduped:
            deduped.append(token)
    return deduped


def _default_detection_patterns(language_policy: dict) -> dict[str, list[str]]:
    template: dict[str, list[str]] = {}
    for lang in _policy_language_codes(language_policy):
        patterns = _LANGUAGE_DETECTION_PATTERNS.get(lang, [])
        if patterns:
            template[lang] = list(patterns)
    if not template:
        template["en"] = list(_LANGUAGE_DETECTION_PATTERNS.get("en", []))
    return template


def _apply_language_policy_templates(language_policy: dict) -> dict:
    normalized = dict(language_policy or {})
    detection = normalized.get("detection_patterns", {})
    detection_map = detection if isinstance(detection, dict) else {}
    merged = {
        str(lang): _dedupe_patterns(list(patterns))
        for lang, patterns in detection_map.items()
        if isinstance(patterns, list)
    }
    for lang, patterns in _default_detection_patterns(normalized).items():
        if not merged.get(lang):
            merged[lang] = list(patterns)
    normalized["detection_patterns"] = merged
    return normalized


def _default_search_intent_policy(language_policy: dict) -> dict:
    request_patterns: list[str] = []
    educational_patterns: list[str] = []
    for lang in _policy_language_codes(language_policy):
        request_patterns.extend(_SEARCH_REQUEST_PATTERNS_BY_LANG.get(lang, []))
        educational_patterns.extend(_SEARCH_EDU_HINT_PATTERNS_BY_LANG.get(lang, []))
    if not request_patterns:
        request_patterns.extend(_SEARCH_REQUEST_PATTERNS_BY_LANG.get("en", []))
    if not educational_patterns:
        educational_patterns.extend(_SEARCH_EDU_HINT_PATTERNS_BY_LANG.get("en", []))
    return {
        "request_patterns": _dedupe_patterns(request_patterns),
        "non_educational_patterns": list(_SEARCH_NON_EDU_PATTERNS),
        "educational_hint_patterns": _dedupe_patterns(educational_patterns),
    }


def _normalize_search_intent_policy(policy: dict | None, language_policy: dict) -> dict:
    template = _default_search_intent_policy(language_policy)
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


def _default_backlog_context(student_id: str, student_name: str = "Student") -> dict:
    language_policy = _apply_language_policy_templates(_default_language_policy())
    search_intent_policy = _default_search_intent_policy(language_policy)
    return {
        "student_id": student_id,
        "student_name": student_name,
        "preferred_language": "en",
        "track_id": "general-track",
        "track_title": "General Learning",
        "topic_id": "current-topic",
        "topic_title": "Current Topic",
        "topic_status": "in_progress",
        "unresolved_topics": 0,
        "available_topics": [],
        "resume_message": "Let's continue where we left off.",
        "language_policy": language_policy,
        "language_contract": _build_language_contract(language_policy),
        "search_intent_policy": search_intent_policy,
    }


async def _register_active_student_session(student_id: str, session_id: str, websocket: WebSocket) -> tuple[str | None, WebSocket | None]:
    """Track active sessions per student and return any prior live socket."""
    previous_session_id: str | None = None
    previous_websocket: WebSocket | None = None
    async with _active_student_sessions_lock:
        previous = _active_student_sessions.get(student_id)
        if previous:
            previous_session_id = str(previous.get("session_id") or "")
            previous_ws = previous.get("websocket")
            if isinstance(previous_ws, WebSocket):
                previous_websocket = previous_ws
        _active_student_sessions[student_id] = {
            "session_id": session_id,
            "websocket": websocket,
        }
    return previous_session_id, previous_websocket


async def _unregister_active_student_session(student_id: str, session_id: str) -> None:
    """Clear active-session tracking only if this session is still current."""
    async with _active_student_sessions_lock:
        active = _active_student_sessions.get(student_id)
        if active and active.get("session_id") == session_id:
            _active_student_sessions.pop(student_id, None)


async def _load_backlog_context(student_id: str) -> dict | None:
    """Load student backlog context from Firestore."""
    if not firestore_client:
        logger.error("Firestore unavailable while loading profile '%s'", student_id)
        return None

    now = time.time()
    student_ref = firestore_client.collection("students").document(student_id)
    student_snapshot = await student_ref.get()
    if not student_snapshot.exists:
        logger.warning("Student profile '%s' not found in Firestore", student_id)
        return None

    student_data = student_snapshot.to_dict() or {}
    student_name = str(student_data.get("name") or "Student")
    default_context = _default_backlog_context(student_id, student_name)

    track_rows: list[dict] = []
    preferred_track_id = str(student_data.get("active_track_id") or "").strip()
    async for track_snapshot in student_ref.collection("tracks").stream():
        track_data = track_snapshot.to_dict() or {}
        track_rows.append({
            "id": track_snapshot.id,
            "title": str(track_data.get("title") or track_snapshot.id),
            "goal": track_data.get("goal") or "",
            "data": track_data,
        })

    language_policy = _normalize_language_policy(student_data.get("language_policy"), _default_language_policy())
    language_policy = _apply_language_policy_templates(language_policy)
    search_intent_policy = _normalize_search_intent_policy(
        student_data.get("search_intent_policy"),
        language_policy,
    )
    preferred_language = _normalize_preferred_language(
        student_data.get("preferred_language") or language_policy.get("l2") or "en"
    )
    student_updates: dict = {}
    if student_data.get("language_policy") != language_policy:
        student_updates["language_policy"] = language_policy
    if student_data.get("search_intent_policy") != search_intent_policy:
        student_updates["search_intent_policy"] = search_intent_policy
    if _normalize_preferred_language(student_data.get("preferred_language")) != preferred_language:
        student_updates["preferred_language"] = preferred_language

    if not track_rows:
        if student_updates:
            student_updates["updated_at"] = now
            await student_ref.set(student_updates, merge=True)
        context = _default_backlog_context(student_id, student_name)
        context.update({
            "preferred_language": preferred_language,
            "previous_notes": [],
            "resume_message": f"Welcome back, {student_name}. Let's set your first learning track.",
            "language_policy": language_policy,
            "language_contract": _build_language_contract(language_policy),
            "search_intent_policy": search_intent_policy,
        })
        return context

    track_rows.sort(key=lambda row: (0 if row["id"] == preferred_track_id else 1, str(row["title"]).lower(), row["id"]))
    active_track = track_rows[0]
    active_track_id = active_track["id"]
    active_track_data = active_track["data"]
    if preferred_track_id != active_track_id:
        student_updates["active_track_id"] = active_track_id

    active_track_ref = student_ref.collection("tracks").document(active_track_id)
    topic_rows: list[dict] = []
    unresolved_topics = 0
    async for topic_snapshot in active_track_ref.collection("topics").stream():
        topic_data = topic_snapshot.to_dict() or {}
        if topic_data.get("checkpoint_open"):
            unresolved_topics += 1
        topic_rows.append({
            "id": topic_snapshot.id,
            "title": str(topic_data.get("title") or topic_snapshot.id),
            "status": str(topic_data.get("status", "not_started")).lower(),
            "order_index": _safe_order_index(topic_data.get("order_index"), 9999),
        })
    topic_rows.sort(key=lambda row: (row["order_index"], row["title"]))

    requested_topic_id = str(student_data.get("last_active_topic_id") or "").strip()
    active_topic = next((row for row in topic_rows if row["id"] == requested_topic_id), None)
    if active_topic is None and topic_rows:
        active_topic = next((row for row in topic_rows if row["status"] != "mastered"), topic_rows[0])

    previous_topic_title = active_topic["title"] if active_topic else default_context["topic_title"]
    if active_topic and active_topic["status"] == "mastered":
        active_index = next((idx for idx, row in enumerate(topic_rows) if row["id"] == active_topic["id"]), -1)
        next_topic = None
        if active_index >= 0:
            for row in topic_rows[active_index + 1:]:
                if row["status"] != "mastered":
                    next_topic = row
                    break
        if not next_topic:
            next_topic = next((row for row in topic_rows if row["status"] != "mastered"), None)
        if next_topic:
            active_topic = next_topic

    if active_topic and requested_topic_id != active_topic["id"]:
        student_updates["last_active_topic_id"] = active_topic["id"]

    if student_updates:
        student_updates["updated_at"] = now
        await student_ref.set(student_updates, merge=True)

    if not active_topic:
        context = _default_backlog_context(student_id, student_name)
        context.update({
            "preferred_language": preferred_language,
            "previous_notes": [],
            "track_id": active_track_id,
            "track_title": str(active_track_data.get("title") or active_track_id),
            "resume_message": f"Welcome back, {student_name}. Let's choose your next topic.",
            "language_policy": language_policy,
            "language_contract": _build_language_contract(language_policy),
            "search_intent_policy": search_intent_policy,
        })
        return context

    topic_title = active_topic["title"]
    topic_status = active_topic["status"]
    if topic_status == "mastered":
        resume_message = (
            f"Welcome back, {student_name}. You already mastered {topic_title}. "
            "Tell me which topic you want to tackle next."
        )
    elif topic_title != previous_topic_title:
        resume_message = (
            f"Welcome back, {student_name}. You mastered {previous_topic_title}. "
            f"Next up: {topic_title}."
        )
    else:
        resume_message = f"Welcome back, {student_name}. Last time we were working on {topic_title}."

    # Load previous notes for the active topic
    previous_notes: list[dict] = []
    if active_topic:
        try:
            notes_ref = (
                student_ref.collection("tracks")
                .document(active_track_id)
                .collection("topics")
                .document(active_topic["id"])
                .collection("notes")
            )
            async for note_snap in notes_ref.stream():
                note_data = note_snap.to_dict() or {}
                note_status = str(note_data.get("status", "")).lower()
                if note_status in ("done", "mastered"):
                    continue
                previous_notes.append({
                    "id": note_snap.id,
                    "title": note_data.get("title", ""),
                    "content": note_data.get("content", ""),
                    "note_type": note_data.get("note_type", "insight"),
                    "status": note_status or "pending",
                })
                if len(previous_notes) >= 20:
                    break
        except Exception:
            logger.warning("Failed to load previous notes for student '%s'", student_id, exc_info=True)

    return {
        "student_id": student_id,
        "student_name": student_name,
        "preferred_language": preferred_language,
        "track_id": active_track_id,
        "track_title": str(active_track_data.get("title") or active_track_id),
        "topic_id": active_topic["id"],
        "topic_title": topic_title,
        "topic_status": topic_status,
        "unresolved_topics": unresolved_topics,
        "available_topics": [
            {"id": row["id"], "title": row["title"], "status": row["status"]}
            for row in topic_rows
        ],
        "previous_notes": previous_notes,
        "resume_message": resume_message,
        "language_policy": language_policy,
        "language_contract": _build_language_contract(language_policy),
        "search_intent_policy": search_intent_policy,
    }


async def _build_profile_summary(student_snapshot) -> dict:
    """Build lightweight profile data for profile picker UI."""
    student_data = student_snapshot.to_dict() or {}
    student_id = student_snapshot.id
    student_ref = student_snapshot.reference
    student_name = str(student_data.get("name") or student_id)
    language_policy = _normalize_language_policy(student_data.get("language_policy"), _default_language_policy())
    language_policy = _apply_language_policy_templates(language_policy)
    preferred_language = _normalize_preferred_language(
        student_data.get("preferred_language") or language_policy.get("l2")
    )
    active_track_id = str(student_data.get("active_track_id") or "").strip()
    active_topic_id = str(student_data.get("last_active_topic_id") or "").strip()

    track_rows: list[dict] = []
    async for track_snapshot in student_ref.collection("tracks").stream():
        track_data = track_snapshot.to_dict() or {}
        track_rows.append({
            "id": track_snapshot.id,
            "title": str(track_data.get("title") or track_snapshot.id),
            "goal": str(track_data.get("goal") or ""),
        })
    track_rows.sort(key=lambda row: (0 if row["id"] == active_track_id else 1, row["title"].lower(), row["id"]))
    chosen_track = track_rows[0] if track_rows else None

    topic_title = ""
    if chosen_track:
        track_ref = student_ref.collection("tracks").document(chosen_track["id"])
        topic_rows: list[dict] = []
        async for topic_snapshot in track_ref.collection("topics").stream():
            topic_data = topic_snapshot.to_dict() or {}
            topic_rows.append({
                "id": topic_snapshot.id,
                "title": str(topic_data.get("title") or topic_snapshot.id),
                "status": str(topic_data.get("status", "not_started")).lower(),
                "order_index": _safe_order_index(topic_data.get("order_index"), 9999),
            })
        topic_rows.sort(key=lambda row: (row["order_index"], row["title"]))
        active_topic = next((row for row in topic_rows if row["id"] == active_topic_id), None)
        if active_topic is None and topic_rows:
            active_topic = next((row for row in topic_rows if row["status"] != "mastered"), topic_rows[0])
        if active_topic:
            topic_title = active_topic["title"]

    focus = str(student_data.get("profile_focus") or "").strip()
    if not focus and chosen_track and chosen_track["goal"]:
        focus = chosen_track["goal"]
    if not focus and topic_title:
        focus = f"Continue with {topic_title}"
    if not focus:
        focus = "Continue learning"

    return {
        "id": student_id,
        "name": student_name,
        "track": chosen_track["title"] if chosen_track else "General Learning",
        "focus": focus,
        "preferred_language": preferred_language,
    }

# Firestore client for session logging (optional — works without it for local dev)
firestore_client = None
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "seeme-tutor")
try:
    from google.cloud import firestore as firestore_module
    firestore_client = firestore_module.AsyncClient(project=GCP_PROJECT_ID)
    logger.info("Firestore client initialized (project=%s)", GCP_PROJECT_ID)
except ImportError:
    logger.info("google-cloud-firestore not installed — session logging disabled (OK for local dev)")
except Exception:
    logger.error(
        "Firestore client failed to initialize for project=%s — session logging disabled. "
        "Check service account credentials and IAM permissions.",
        GCP_PROJECT_ID,
        exc_info=True,
    )
# ---------------------------------------------------------------------------
# ADK Runner + Session Service
# ---------------------------------------------------------------------------
session_service = InMemorySessionService()
runner = Runner(
    app_name="seeme_tutor",
    agent=tutor_agent,
    session_service=session_service,
)

ADK_RUN_CONFIG = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    response_modalities=["AUDIO"],
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name="Puck",
            ),
        ),
    ),
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=300,
            silence_duration_ms=700,
        ),
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
)

FRONTEND_DIR = BASE_DIR.parent / "frontend"
if not FRONTEND_DIR.is_dir():
    FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="SeeMe Tutor",
    description="Real-time multimodal AI tutoring via Gemini Live API.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
logger.info(
    "Security config: cors_origins=%s cors_credentials=%s csp_enabled=%s http_rate=%d/%ss ws_rate=%d/%ss",
    CORS_ALLOWED_ORIGINS,
    CORS_ALLOW_CREDENTIALS,
    SECURITY_CSP_ENABLED,
    HTTP_RATE_LIMIT_MAX,
    HTTP_RATE_LIMIT_WINDOW_S,
    WS_CONNECT_RATE_LIMIT_MAX,
    WS_CONNECT_RATE_LIMIT_WINDOW_S,
)


def _should_rate_limit_http(path: str) -> bool:
    if path == "/health":
        return False
    if path.startswith("/static/"):
        return False
    return True


@app.middleware("http")
async def http_security_middleware(request: Request, call_next):
    path = request.url.path
    if _should_rate_limit_http(path):
        client_ip = extract_client_ip(
            request.headers.get("x-forwarded-for"),
            request.client.host if request.client else None,
        )
        allowed = await _http_rate_limiter.allow(
            f"http:{client_ip}",
            limit=HTTP_RATE_LIMIT_MAX,
            window_seconds=HTTP_RATE_LIMIT_WINDOW_S,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please retry shortly."},
                headers={"Retry-After": str(HTTP_RATE_LIMIT_WINDOW_S)},
            )

    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    logger.info("Serving frontend static files from %s", FRONTEND_DIR)
else:
    logger.warning("Frontend directory not found at %s — static serving disabled", FRONTEND_DIR)


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    """Serve the frontend single-page application."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_path))


@app.get("/health")
async def health_check() -> dict:
    """Liveness probe for Cloud Run."""
    return {"status": "ok", "service": "seeme-tutor"}


@app.get("/api/profiles")
async def list_profiles() -> dict:
    """Return profile cards from Firestore for frontend profile picker."""
    if not firestore_client:
        raise HTTPException(status_code=503, detail="Profile storage unavailable. Configure Firestore first.")

    profiles: list[dict] = []
    async for student_snapshot in firestore_client.collection("students").stream():
        try:
            profile = await _build_profile_summary(student_snapshot)
            if profile.get("id"):
                profiles.append(profile)
        except Exception:
            logger.warning(
                "Failed to build profile summary for student '%s'",
                student_snapshot.id,
                exc_info=True,
            )

    profiles.sort(key=lambda p: (str(p.get("name", "")).lower(), str(p.get("id", ""))))
    return {"profiles": profiles}


class _StudentEndedSession(Exception):
    """Raised by _forward_to_gemini when the student explicitly ends the session."""


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Full-duplex WebSocket bridge between the browser and Gemini Live API.

    Browser → server message format (JSON):
        {"type": "audio", "data": "<base64-encoded PCM 16-bit 16 kHz>"}
        {"type": "video", "data": "<base64-encoded JPEG>"}

    Server → browser message format (JSON):
        {"type": "audio", "data": "<base64-encoded PCM 16-bit 24 kHz>"}
        {"type": "text", "data": "<transcript segment>"}
        {"type": "turn_complete"}
        {"type": "interrupted"}
        {"type": "session_limit"}
        {"type": "error", "data": "<description>"}
    """
    client_host = extract_client_ip(
        websocket.headers.get("x-forwarded-for"),
        websocket.client.host if websocket.client else None,
    )
    ws_allowed = await _ws_rate_limiter.allow(
        f"ws:{client_host}",
        limit=WS_CONNECT_RATE_LIMIT_MAX,
        window_seconds=WS_CONNECT_RATE_LIMIT_WINDOW_S,
    )
    if not ws_allowed:
        logger.warning("Rejected WebSocket from %s: rate limit exceeded", client_host)
        await websocket.close(code=1013)
        return

    await websocket.accept()

    if DEMO_ACCESS_CODE:
        client_code = websocket.query_params.get("code", "")
        if client_code != DEMO_ACCESS_CODE:
            logger.warning("Rejected connection: invalid demo access code")
            await _send_json(websocket, {"type": "error", "data": "Invalid demo access code. Please reload and try again."})
            await websocket.close(code=1008)
            return

    raw_student_id = websocket.query_params.get("student_id", "").strip().lower()
    if not raw_student_id:
        await _send_json(websocket, {"type": "error", "data": "Please select a student profile before starting."})
        await websocket.close(code=1008)
        return
    if not _STUDENT_ID_PATTERN.match(raw_student_id):
        logger.warning("Rejected connection: invalid student_id format '%s'", raw_student_id)
        await _send_json(websocket, {"type": "error", "data": "Invalid profile identifier. Please select a profile again."})
        await websocket.close(code=1008)
        return

    backlog_context = await _load_backlog_context(raw_student_id)
    if not backlog_context:
        await _send_json(
            websocket,
            {
                "type": "error",
                "data": "Profile not found in Firestore. Please create this student profile before starting.",
            },
        )
        await websocket.close(code=1008)
        return

    session_id = str(uuid.uuid4())
    session_start = time.time()
    logger.info("Session %s accepted from %s", session_id, client_host)

    # Log session start to Firestore
    if firestore_client:
        try:
            await firestore_client.collection("sessions").document(session_id).set({
                "started_at": session_start,
                "client_host": _anonymize_ip(client_host),
                "ended_reason": None,
                "duration_seconds": None,
                "consent_given": False,
                "student_id": raw_student_id,
                "track_id": backlog_context.get("track_id"),
                "topic_id": backlog_context.get("topic_id"),
            })
        except Exception:
            logger.warning("Session %s: failed to log start to Firestore", session_id, exc_info=True)

    session_state = {
        "session_id": session_id,
        "gcp_project_id": GCP_PROJECT_ID,
        "student_id": raw_student_id,
        "student_name": backlog_context.get("student_name"),
        "preferred_language": backlog_context.get("preferred_language"),
        "track_id": backlog_context.get("track_id"),
        "track_title": backlog_context.get("track_title"),
        "topic_id": backlog_context.get("topic_id"),
        "topic_title": backlog_context.get("topic_title"),
        "topic_status": backlog_context.get("topic_status"),
        "available_topics": backlog_context.get("available_topics", []),
        "language_policy": backlog_context.get("language_policy"),
        "language_contract": backlog_context.get("language_contract"),
        "search_intent_policy": backlog_context.get("search_intent_policy"),
        "previous_notes": backlog_context.get("previous_notes", []),
        "resume_message": backlog_context.get("resume_message"),
        "session_phase": "greeting",
    }
    report = create_report(session_id, raw_student_id)
    await _send_json(websocket, {"type": "backlog_context", "data": backlog_context})
    report.record_backlog_sent()
    for note in backlog_context.get("previous_notes", []):
        await _send_json(websocket, {"type": "whiteboard", "data": note})

    replaced_session_id, replaced_socket = await _register_active_student_session(
        raw_student_id,
        session_id,
        websocket,
    )
    if replaced_socket and replaced_socket is not websocket:
        logger.info(
            "Session %s replaced prior session %s for student '%s'",
            session_id,
            replaced_session_id or "unknown",
            raw_student_id,
        )
        await _send_json(replaced_socket, {
            "type": "error",
            "data": "This profile started a new session in another tab or device. Closing this older session.",
        })
        try:
            await replaced_socket.close(code=1000)
        except Exception:
            logger.debug(
                "Failed to close replaced socket for student '%s'",
                raw_student_id,
                exc_info=True,
            )

    _latency_state[session_id] = {"last_audio_in": 0.0, "awaiting_first_response": False}
    _debug_counters[session_id] = {
        "audio_in": 0, "audio_out": 0, "video_in": 0,
        "text_out": 0, "turn_complete": 0, "interrupted": 0,
        "tool_responses": 0, "last_gemini_event_at": 0.0,
        "last_audio_in_at": 0.0,
    }
    runtime_state = {
        "last_user_activity_at": time.time(),
        "idle_stage": 0,  # 0=none, 1=first check-in sent, 2=second check-in sent
        "away_mode": False,
        "speech_pace": "normal",
        "assistant_speaking": False,
        "conversation_started": False,
        "mic_active": False,
        "mic_opened_at": None,
        "mic_kickoff_sent": False,
        "_greeting_delivered": False,  # True after first greeting turn completes
        "_student_has_spoken": False,   # True after first input_transcription
        "_turn_ticket_count": 1,        # Allow one tutor turn per student/proactive trigger
        "_last_tutor_audio_at": 0.0,    # For echo-guard around input_transcription
        "awaiting_student_reply": False,
        "student_activity_count": 0,
        "last_tutor_prompt_text": "",
        "last_tutor_prompt_activity_count": -1,
        "suspected_repetition_count": 0,
        "question_like_streak": 0,
        "last_forced_search_query": "",
        "last_forced_search_at": 0.0,
        "search_intent_policy": backlog_context.get("search_intent_policy"),
        "resume_message": backlog_context.get("resume_message"),
        "student_id": raw_student_id,
        "student_name": backlog_context.get("student_name"),
        "track_id": backlog_context.get("track_id"),
        **init_proactive_state(),
        **init_screen_share_state(),
        **init_whiteboard_state(),
        **init_guardrails_state(),
        **init_grounding_state(),
        **init_language_state(
            backlog_context.get("language_policy"),
            backlog_context.get("preferred_language"),
        ),
        **init_latency_state(session_start),
        "topic_id": backlog_context.get("topic_id"),
        "topic_title": backlog_context.get("topic_title"),
        "_report": report,
    }
    wb_queue = register_whiteboard_queue(session_id)
    topic_queue = register_topic_update_queue(session_id)
    ended_reason = "disconnect"
    try:
        try:
            # Create ADK session with initial state
            adk_session = await session_service.create_session(
                app_name="seeme_tutor",
                user_id=raw_student_id,
                state=session_state,
                session_id=session_id,
            )

            # Create queue for browser→Gemini upstream
            live_queue = LiveRequestQueue()

            # Send [SESSION START] hidden turn with student context
            student_context = {
                "student_name": session_state.get("student_name"),
                "preferred_language": session_state.get("preferred_language"),
                "resume_message": session_state.get("resume_message"),
                "topic_title": session_state.get("topic_title"),
                "topic_status": session_state.get("topic_status"),
                "language_contract": session_state.get("language_contract"),
                "previous_notes_count": len(session_state.get("previous_notes", [])),
            }
            import json as _json
            live_queue.send_content(
                types.Content(
                    role="user",
                    parts=[types.Part(text=(
                        "[SESSION START — CONTEXT ONLY, DO NOT SPEAK]\n"
                        + _json.dumps(student_context, ensure_ascii=False)
                        + "\n[IMPORTANT: This is background context only. "
                        "Do NOT generate any audio or text response to this message. "
                        "Wait silently until the student speaks to you via their microphone. "
                        "When the student speaks, greet them naturally and begin the session.]"
                    ))],
                )
            )

            forward_task = asyncio.create_task(
                _forward_to_gemini(websocket, live_queue, session_id, runtime_state, report),
                name="forward_to_gemini",
            )
            receive_task = asyncio.create_task(
                _forward_to_client(websocket, runner, live_queue, session_id, runtime_state, topic_queue, report),
                name="forward_to_client",
            )
            timer_task = asyncio.create_task(
                _session_timer(websocket, SESSION_TIMEOUT_SECONDS),
                name="session_timer",
            )
            idle_task = asyncio.create_task(
                proactive_idle_orchestrator(websocket, live_queue, runtime_state),
                name="idle_orchestrator",
            )
            heartbeat_task = asyncio.create_task(
                _session_heartbeat(session_id, runtime_state),
                name="session_heartbeat",
            )
            wb_dispatcher_task = asyncio.create_task(
                whiteboard_dispatcher(websocket, wb_queue, runtime_state),
                name="whiteboard_dispatcher",
            )

            done, pending = await asyncio.wait(
                {forward_task, receive_task, timer_task, idle_task, heartbeat_task, wb_dispatcher_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("Error while cancelling task %s", task.get_name())

            for task in done:
                try:
                    exc = task.exception()
                except asyncio.CancelledError:
                    logger.debug("Task %s was cancelled", task.get_name())
                    continue
                if task is timer_task and exc is None:
                    ended_reason = "limit"
                elif isinstance(exc, _StudentEndedSession):
                    ended_reason = "student_ended"
                elif exc is not None:
                    exc_name = type(exc).__name__
                    if "Disconnect" not in exc_name and "Closed" not in exc_name:
                        logger.error("Task %s crashed: %s", task.get_name(), exc, exc_info=exc)
                        if ended_reason == "disconnect":
                            ended_reason = "error"
                    else:
                        logger.info("Task %s ended normally (client disconnect)", task.get_name())

        except Exception as exc:
            logger.exception("Session %s: ADK runner error: %s", session_id, exc)
            await _send_json(websocket, {
                "type": "error",
                "data": "Could not connect to the tutoring service. Please try again in a moment.",
            })
            ended_reason = "gemini_error"

    finally:
        await _unregister_active_student_session(raw_student_id, session_id)
        unregister_whiteboard_queue(session_id)
        unregister_topic_update_queue(session_id)
        _latency_state.pop(session_id, None)
        _debug_counters.pop(session_id, None)
        duration = int(time.time() - session_start)
        if firestore_client:
            try:
                await firestore_client.collection("sessions").document(session_id).update({
                    "ended_reason": ended_reason,
                    "duration_seconds": duration,
                })
            except Exception:
                logger.warning("Session %s: failed to log end to Firestore", session_id, exc_info=True)

            # Copy meaningful notes to student's topic-level backlog
            try:
                copyable_statuses = {"pending", "done", "mastered", "struggling", "in_progress"}
                student_id = runtime_state.get("student_id")
                track_id = runtime_state.get("track_id")
                topic_id = runtime_state.get("topic_id")
                if student_id and track_id and topic_id:
                    notes_ref = firestore_client.collection("sessions").document(session_id).collection("notes")
                    async for note_snapshot in notes_ref.stream():
                        note_data = note_snapshot.to_dict() or {}
                        note_status = str(note_data.get("status", "")).lower()
                        if note_status not in copyable_statuses:
                            continue
                        note_title = str(note_data.get("title", ""))
                        note_type = str(note_data.get("note_type", "insight"))
                        # Deterministic ID so repeated copies overwrite instead of duplicating
                        stable_id = hashlib.md5(f"{note_type}:{note_title.strip().lower()}".encode()).hexdigest()[:16]
                        dest_ref = (
                            firestore_client.collection("students")
                            .document(student_id)
                            .collection("tracks")
                            .document(track_id)
                            .collection("topics")
                            .document(note_data.get("topic_id") or topic_id)
                            .collection("notes")
                            .document(stable_id)
                        )
                        await dest_ref.set({
                            "title": note_title,
                            "content": note_data.get("content", ""),
                            "note_type": note_type,
                            "status": note_status,
                            "source_session_id": session_id,
                            "created_at": note_data.get("created_at"),
                            "updated_at": note_data.get("updated_at"),
                        })
            except Exception:
                logger.warning("Session %s: failed to copy notes to student backlog", session_id, exc_info=True)

        logger.info("Session %s ended after %ds (reason: %s)", session_id, duration, ended_reason)
        try:
            logger.info(
                "Session %s %s",
                session_id,
                format_latency_summary(
                    runtime_state,
                    turns=int(report.data["turns"]["count"]) if report else 0,
                ),
            )
        except Exception:
            logger.debug("Session %s: failed to format latency summary", session_id, exc_info=True)

        # Save test report
        if report:
            report.finalize(ended_reason)
            try:
                report.save()
            except Exception:
                logger.warning("Session %s: failed to save test report", session_id, exc_info=True)
            remove_report(session_id)


async def _session_heartbeat(session_id: str, runtime_state: dict) -> None:
    """Log session state every 3 seconds for debugging silence issues."""
    d = _debug_logger
    prev_counters: dict = {}
    while True:
        await asyncio.sleep(3.0)
        c = _debug_counters.get(session_id)
        if c is None:
            return
        now = time.time()
        since_gemini = now - c["last_gemini_event_at"] if c["last_gemini_event_at"] > 0 else -1
        since_audio_in = now - c["last_audio_in_at"] if c["last_audio_in_at"] > 0 else -1
        # Deltas since last heartbeat
        d_ai = c["audio_in"] - prev_counters.get("audio_in", 0)
        d_ao = c["audio_out"] - prev_counters.get("audio_out", 0)
        d_vi = c["video_in"] - prev_counters.get("video_in", 0)
        d_to = c["text_out"] - prev_counters.get("text_out", 0)
        d_tc = c["turn_complete"] - prev_counters.get("turn_complete", 0)
        d_ir = c["interrupted"] - prev_counters.get("interrupted", 0)
        d_tr = c["tool_responses"] - prev_counters.get("tool_responses", 0)
        prev_counters = dict(c)

        d.debug(
            "HEARTBEAT sid=%s | in:audio=%d video=%d | out:audio=%d text=%d tc=%d int=%d tool=%d | "
            "speaking=%s conv=%s mic=%s away=%s idle=%d | "
            "since_gemini=%.1fs since_audio_in=%.1fs",
            session_id[:8],
            d_ai, d_vi,
            d_ao, d_to, d_tc, d_ir, d_tr,
            runtime_state.get("assistant_speaking"),
            runtime_state.get("conversation_started"),
            runtime_state.get("mic_active"),
            runtime_state.get("away_mode"),
            runtime_state.get("idle_stage", 0),
            since_gemini, since_audio_in,
        )
        # Flag potential silence: no Gemini events for >5s while audio is flowing in
        if since_gemini > 5 and d_ai > 0:
            d.warning(
                "SILENCE sid=%s — no Gemini events for %.1fs but %d audio chunks sent | "
                "speaking=%s away=%s idle=%d",
                session_id[:8], since_gemini, d_ai,
                runtime_state.get("assistant_speaking"),
                runtime_state.get("away_mode"),
                runtime_state.get("idle_stage", 0),
            )


async def _session_timer(websocket: WebSocket, timeout: float) -> None:
    """Send session_limit after the timeout expires, ending the session gracefully."""
    await asyncio.sleep(timeout)
    logger.info("Session timeout reached (%ds) — notifying client", int(timeout))
    try:
        await websocket.send_text(json.dumps({"type": "session_limit"}))
    except Exception:
        logger.warning(
            "Could not deliver session_limit to client (WebSocket already closed)",
            exc_info=True,
        )


def _mark_student_activity(runtime_state: dict, *, unlock_turn: bool = False) -> None:
    """Common state reset when the student does something intentional.

    Called from every upstream control-message handler that represents real
    student engagement (speech, button press, source switch, etc.).  Centralises
    the six-line pattern that was copy-pasted across 8+ handlers.

    Args:
        unlock_turn: If True, also guarantee at least one turn ticket so the
            tutor can respond (used for barge-in, speech pace, transcription).
    """
    runtime_state["last_user_activity_at"] = time.time()
    runtime_state["idle_stage"] = 0
    runtime_state["conversation_started"] = True
    runtime_state["mic_kickoff_sent"] = True
    runtime_state["proactive_waiting_for_student"] = False
    runtime_state["awaiting_student_reply"] = False
    runtime_state["student_activity_count"] = int(runtime_state.get("student_activity_count", 0)) + 1
    reset_silence_tracking(runtime_state)
    if unlock_turn:
        runtime_state["_turn_ticket_count"] = max(
            int(runtime_state.get("_turn_ticket_count", 0)), 1,
        )


def _mark_proactive_response_seen(runtime_state: dict, *, source: str) -> None:
    """Lock proactive idle injections after a proactive-guided tutor response."""
    if runtime_state.get("idle_poke_sent") or runtime_state.get("idle_nudge_sent"):
        if not runtime_state.get("proactive_waiting_for_student"):
            logger.info("Proactive cycle locked after tutor %s output; waiting for student activity", source)
        runtime_state["proactive_waiting_for_student"] = True


def _is_probable_speech_pcm16(raw_bytes: bytes) -> bool:
    """Heuristic speech detection for 16kHz PCM16 mono chunks."""
    if not raw_bytes or len(raw_bytes) < 160:
        return False
    try:
        rms = audioop.rms(raw_bytes, 2)
        peak = audioop.max(raw_bytes, 2)
    except Exception:
        return False
    return rms >= 420 or peak >= 1700


async def _resume_from_away(websocket: WebSocket, runtime_state: dict) -> None:
    runtime_state["away_mode"] = False
    _mark_student_activity(runtime_state)
    await _send_json(websocket, {"type": "assistant_state", "data": {"state": "active", "reason": "resume"}})
    if runtime_state.get("mic_active"):
        resume_message = runtime_state.get("resume_message") or "Welcome back. Let's continue from your last checkpoint."
        await _send_json(websocket, {"type": "assistant_prompt", "data": resume_message})


async def _apply_checkpoint_decision(runtime_state: dict, session_id: str, decision: str) -> dict:
    """Persist checkpoint decision for the active student topic."""
    normalized = (decision or "").strip().lower()
    if normalized not in {"now", "later", "resolved"}:
        return {"result": "error", "detail": "decision must be now, later, or resolved"}

    student_id = runtime_state.get("student_id")
    track_id = runtime_state.get("track_id")
    topic_id = runtime_state.get("topic_id")
    if not student_id or not track_id or not topic_id:
        return {"result": "error", "detail": "checkpoint context missing in runtime state"}

    if not firestore_client:
        return {"result": "saved", "decision": normalized, "persisted": False}

    now = time.time()
    checkpoint_id = f"{track_id}--{topic_id}"
    checkpoint_status = "open"
    topic_status = "struggling"
    checkpoint_open = True
    if normalized == "now":
        checkpoint_status = "in_progress"
        topic_status = "in_progress"
    elif normalized == "later":
        checkpoint_status = "deferred"
        topic_status = "struggling"
    elif normalized == "resolved":
        checkpoint_status = "resolved"
        topic_status = "mastered"
        checkpoint_open = False

    student_ref = firestore_client.collection("students").document(student_id)
    checkpoint_ref = student_ref.collection("checkpoints").document(checkpoint_id)
    topic_ref = (
        student_ref.collection("tracks")
        .document(track_id)
        .collection("topics")
        .document(topic_id)
    )

    await checkpoint_ref.set({
        "status": checkpoint_status,
        "decision": normalized,
        "updated_at": now,
        "decision_at": now,
        "session_id": session_id,
    }, merge=True)
    await topic_ref.set({
        "status": topic_status,
        "checkpoint_open": checkpoint_open,
        "last_seen_session_id": session_id,
        "last_seen_at": now,
        "updated_at": now,
    }, merge=True)
    await firestore_client.collection("sessions").document(session_id).collection("progress").add({
        "student_id": student_id,
        "track_id": track_id,
        "topic_id": topic_id,
        "status": f"checkpoint_{normalized}",
        "timestamp": now,
    })
    return {
        "result": "saved",
        "decision": normalized,
        "persisted": True,
        "checkpoint_id": checkpoint_id,
    }


async def _log_command_event(session_id: str, runtime_state: dict, payload: dict) -> None:
    """Persist voice command telemetry for debugging command behavior."""
    if not isinstance(payload, dict):
        payload = {}

    command_event = {
        "timestamp": time.time(),
        "source": "voice",
        "student_id": runtime_state.get("student_id"),
        "track_id": runtime_state.get("track_id"),
        "topic_id": runtime_state.get("topic_id"),
        "command_id": str(payload.get("command_id", "unknown")),
        "spoken_text": str(payload.get("spoken_text", "")),
        "normalized_text": str(payload.get("normalized_text", "")),
        "match_status": str(payload.get("match_status", "unknown")),
        "action_status": str(payload.get("action_status", "unknown")),
        "detail": str(payload.get("detail", "")),
        "attempt": int(payload.get("attempt", 0) or 0),
        "intent": str(payload.get("intent", "")),
    }
    intent = str(command_event.get("intent") or "").strip().lower()
    if intent == "search_request":
        runtime_state["search_intent_signal_until"] = (
            time.time() + float(SEARCH_INTENT_SIGNAL_WINDOW_S)
        )
    logger.info(
        "CMD session=%s command=%s match=%s action=%s detail=%s attempt=%s normalized='%s'",
        session_id,
        command_event["command_id"],
        command_event["match_status"],
        command_event["action_status"],
        command_event["detail"],
        command_event["attempt"],
        command_event["normalized_text"],
    )

    if not firestore_client:
        return

    try:
        await firestore_client.collection("sessions").document(session_id).collection("commands").add(command_event)
    except Exception:
        logger.warning("Session %s: failed to persist command event", session_id, exc_info=True)


async def _forward_to_gemini(
    websocket: WebSocket,
    queue: LiveRequestQueue,
    session_id: str,
    runtime_state: dict,
    report: "SessionReport | None" = None,
) -> None:
    """
    Receive JSON messages from the browser and forward media to Gemini
    via the ADK LiveRequestQueue.

    Runs until the WebSocket is disconnected or an unrecoverable error occurs.
    """
    audio_chunks_sent = 0
    video_frames_sent = 0
    last_stats_time = time.time()
    STATS_INTERVAL = 10  # log stats every 10 seconds

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received non-JSON message from browser, ignoring")
                continue

            msg_type = message.get("type")

            if not msg_type:
                logger.warning("Malformed browser message (missing type), ignoring")
                continue

            # Control messages (no data payload)
            if msg_type == "end_session":
                logger.info("Student requested end_session")
                queue.close()
                raise _StudentEndedSession()
            if msg_type == "consent_ack":
                logger.info("Session %s: consent acknowledged", session_id)
                if firestore_client:
                    try:
                        await firestore_client.collection("sessions").document(session_id).update({
                            "consent_given": True,
                            "consent_at": time.time(),
                        })
                    except Exception:
                        logger.warning("Session %s: failed to record consent", session_id, exc_info=True)
                continue
            if msg_type == "user_activity":
                activity = message.get("data") if isinstance(message.get("data"), dict) else {}
                activity_kind = str(activity.get("kind", "")).strip().lower()
                # Frontend "speech" activity is a coarse RMS signal and can fire on noise.
                # Do not treat it as real student intent/activity for turn unlocking.
                if activity_kind == "speech":
                    continue
                _mark_student_activity(runtime_state)
                if runtime_state.get("away_mode"):
                    await _resume_from_away(websocket, runtime_state)
                continue
            if msg_type == "mic_start":
                now = time.time()
                runtime_state["mic_active"] = True
                runtime_state["mic_opened_at"] = now
                # mic_start deliberately resets conversation to allow the
                # kickoff timer to fire — do NOT use _mark_student_activity here.
                runtime_state["last_user_activity_at"] = now
                runtime_state["idle_stage"] = 0
                runtime_state["mic_kickoff_sent"] = False
                runtime_state["conversation_started"] = False
                runtime_state["proactive_waiting_for_student"] = False
                reset_silence_tracking(runtime_state)
                try:
                    queue.send_activity_start()
                except Exception:
                    logger.debug("Session %s: failed to send activity_start", session_id, exc_info=True)
                logger.info("Control message from browser: 'mic_start'")
                continue
            if msg_type == "away_mode":
                active = bool(message.get("active", True))
                runtime_state["away_mode"] = active
                if not active:
                    _mark_student_activity(runtime_state)
                else:
                    runtime_state["last_user_activity_at"] = time.time()
                    runtime_state["idle_stage"] = 0
                if active:
                    await _send_json(websocket, {"type": "assistant_state", "data": {"state": "away", "reason": "student_requested"}})
                    await _send_json(websocket, {
                        "type": "assistant_prompt",
                        "data": "Got it — take your time. Say 'I'm back' when you want to continue.",
                    })
                else:
                    await _resume_from_away(websocket, runtime_state)
                continue
            if msg_type == "checkpoint_decision":
                decision = str(message.get("decision", "")).strip().lower()
                result = await _apply_checkpoint_decision(runtime_state, session_id, decision)
                if result.get("result") != "saved":
                    await _send_json(websocket, {
                        "type": "assistant_prompt",
                        "data": "I couldn't save that checkpoint decision yet. Please try again.",
                    })
                    continue
                if decision == "later":
                    await _send_json(websocket, {
                        "type": "assistant_prompt",
                        "data": "Saved for later. We'll keep this topic in your backlog.",
                    })
                elif decision == "now":
                    await _send_json(websocket, {
                        "type": "assistant_prompt",
                        "data": "Great, let's solve it now together.",
                    })
                elif decision == "resolved":
                    await _send_json(websocket, {
                        "type": "assistant_prompt",
                        "data": "Nice work. Marked as resolved in your backlog.",
                    })
                continue
            if msg_type == "speech_pace":
                pace = str(message.get("pace", "")).strip().lower()
                instruction = PACE_CONTROL_INSTRUCTIONS.get(pace)
                if not instruction:
                    logger.warning("Session %s: unsupported speech pace command '%s'", session_id, pace)
                    continue
                runtime_state["speech_pace"] = pace
                _mark_student_activity(runtime_state, unlock_turn=True)
                try:
                    queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=instruction)])
                    )
                except Exception:
                    logger.warning("Session %s: failed to forward speech pace command", session_id, exc_info=True)
                continue
            if msg_type == "barge_in":
                _mark_student_activity(runtime_state, unlock_turn=True)
                runtime_state["_student_has_spoken"] = True
                if float(runtime_state.get("latency_first_request_at", 0.0)) <= 0:
                    runtime_state["latency_first_request_at"] = time.time()
                runtime_state["assistant_speaking"] = False
                runtime_state["latency_last_barge_in_at"] = time.time()
                await _send_json(websocket, {"type": "interrupted"})
                continue
            if msg_type == "command_event":
                await _log_command_event(session_id, runtime_state, message.get("data", {}))
                continue
            if msg_type in ("mic_stop", "camera_off"):
                if msg_type == "mic_stop":
                    runtime_state["mic_active"] = False
                    runtime_state["mic_opened_at"] = None
                    runtime_state["mic_kickoff_sent"] = False
                    runtime_state["idle_stage"] = 0
                    try:
                        queue.send_activity_end()
                    except Exception:
                        logger.debug("Session %s: failed to send activity_end", session_id, exc_info=True)
                logger.info("Control message from browser: '%s'", msg_type)
                continue
            if msg_type == "source_switch":
                now = time.time()
                new_source = str(message.get("source", "")).strip().lower()
                if new_source not in ("screen", "camera"):
                    logger.warning("Session %s: invalid source_switch source '%s'", session_id, new_source)
                    continue
                old_source = runtime_state.get("active_source", "camera")
                # Debounce rapid duplicate switches
                last_switch = runtime_state.get("last_switch_at", 0.0)
                if last_switch > 0 and (now - last_switch) < SOURCE_SWITCH_COOLDOWN_S and old_source == new_source:
                    continue
                runtime_state["active_source"] = new_source
                runtime_state["source_switches"] = runtime_state.get("source_switches", 0) + 1
                runtime_state["last_switch_at"] = now
                _mark_student_activity(runtime_state, unlock_turn=True)
                logger.info(
                    "SOURCE SWITCH #%d: %s -> %s",
                    runtime_state["source_switches"], old_source, new_source,
                )
                if report:
                    report.record_source_switch(old_source, new_source)
                prompt = get_switch_prompt(new_source)
                if prompt:
                    try:
                        queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=prompt)])
                        )
                        runtime_state["last_hidden_prompt_at"] = now
                    except Exception:
                        logger.warning("Session %s: failed to send source switch prompt", session_id, exc_info=True)
                await _send_json(websocket, {
                    "type": "source_switch_ack",
                    "data": {"source": new_source, "count": runtime_state["source_switches"]},
                })
                continue
            if msg_type == "stop_sharing":
                now = time.time()
                old_source = runtime_state.get("active_source", "camera")
                runtime_state["active_source"] = "none"
                runtime_state["stop_sharing_count"] = runtime_state.get("stop_sharing_count", 0) + 1
                _mark_student_activity(runtime_state, unlock_turn=True)
                logger.info("STOP SHARING #%d: was=%s", runtime_state["stop_sharing_count"], old_source)
                if report:
                    report.record_stop_sharing(old_source)
                try:
                    queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=STOP_SHARING_PROMPT)])
                    )
                    runtime_state["last_hidden_prompt_at"] = now
                except Exception:
                    logger.warning("Session %s: failed to send stop sharing prompt", session_id, exc_info=True)
                await _send_json(websocket, {
                    "type": "stop_sharing_ack",
                    "data": {"count": runtime_state["stop_sharing_count"]},
                })
                continue

            encoded_data = message.get("data")
            if not encoded_data:
                logger.warning("Malformed browser message (missing data for type '%s'), ignoring", msg_type)
                continue

            try:
                raw_bytes = base64.b64decode(encoded_data)
            except binascii.Error:
                logger.warning(
                    "Invalid base64 data in browser message of type '%s' (len=%d) — ignoring frame",
                    msg_type,
                    len(encoded_data) if isinstance(encoded_data, str) else -1,
                )
                continue

            if msg_type == "audio":
                now = time.time()
                # Greeting-gate race fix: open the gate as soon as real speech audio arrives,
                # even if input_transcription.finished lands later in the stream.
                if (
                    runtime_state.get("_greeting_delivered")
                    and not runtime_state.get("_student_has_spoken")
                    and _is_probable_speech_pcm16(raw_bytes)
                ):
                    runtime_state["_student_has_spoken"] = True
                    runtime_state["proactive_waiting_for_student"] = False
                    runtime_state["_turn_ticket_count"] = max(int(runtime_state.get("_turn_ticket_count", 0)), 1)
                    logger.info("Detected non-silent student audio — opening greeting gate")
                # NOTE: Raw audio chunks are continuous (~15/sec) even during silence.
                # Do NOT reset idle/silence tracking here — that must only happen on
                # actual speech (input_transcription) or deliberate user actions.
                lat = _latency_state.get(session_id)
                if lat is not None:
                    lat["last_audio_in"] = now
                    lat["awaiting_first_response"] = True
                runtime_state["latency_last_audio_in_at"] = now
                dc = _debug_counters.get(session_id)
                if dc is not None:
                    dc["audio_in"] += 1
                    dc["last_audio_in_at"] = now
                queue.send_realtime(types.Blob(data=raw_bytes, mime_type="audio/pcm;rate=16000"))
                audio_chunks_sent += 1
                if report:
                    report.record_audio_in()
            elif msg_type in ("video", "screen_frame"):
                runtime_state["last_video_frame_at"] = time.time()
                if msg_type == "screen_frame":
                    runtime_state["last_screen_frame_at"] = time.time()
                dc = _debug_counters.get(session_id)
                if dc is not None:
                    dc["video_in"] += 1
                queue.send_realtime(types.Blob(data=raw_bytes, mime_type="image/jpeg"))
                video_frames_sent += 1
                if report:
                    report.record_video_in()
            else:
                logger.warning("Unknown message type from browser: '%s'", msg_type)

            # Periodic stats logging
            now = time.time()
            if now - last_stats_time >= STATS_INTERVAL:
                elapsed = now - last_stats_time
                logger.info(
                    "Session %s — input stats (last %.0fs): audio_chunks=%d (%.1f/s), video_frames=%d",
                    session_id, elapsed, audio_chunks_sent, audio_chunks_sent / elapsed, video_frames_sent,
                )
                audio_chunks_sent = 0
                video_frames_sent = 0
                last_stats_time = now

    except WebSocketDisconnect:
        logger.info("Browser disconnected (forward_to_gemini)")
        queue.close()
    except _StudentEndedSession:
        raise
    except Exception as exc:
        logger.exception("Unexpected error in forward_to_gemini: %s", exc)
        queue.close()
        if report:
            report.record_error(f"forward_to_gemini: {exc}")
        await _send_json(websocket, {
            "type": "error",
            "data": "The connection to the tutor was interrupted. Please refresh to start a new session.",
        })


async def _iter_runner_events_with_retry(
    websocket: WebSocket,
    adk_runner: Runner,
    user_id: str,
    session_id: str,
    live_queue: LiveRequestQueue,
    report: "SessionReport | None" = None,
):
    """Yield ADK stream events with a single reconnect retry on stream errors."""
    attempt = 0
    while True:
        sent_reconnected = False
        try:
            async for event in adk_runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_queue,
                run_config=ADK_RUN_CONFIG,
            ):
                if attempt > 0 and not sent_reconnected:
                    sent_reconnected = True
                    await _send_json(
                        websocket,
                        {
                            "type": "assistant_state",
                            "data": {"state": "active", "reason": "reconnected", "attempt": attempt},
                        },
                    )
                    if report:
                        report.record_stream_reconnect_success(attempt)
                yield event
            return
        except WebSocketDisconnect:
            raise
        except Exception as exc:
            if attempt >= ADK_STREAM_MAX_RETRIES:
                if report:
                    report.record_stream_reconnect_failure(attempt, str(exc))
                raise
            attempt += 1
            if report:
                report.record_stream_retry_attempt(attempt, str(exc))
            logger.warning(
                "Session %s: ADK stream error (attempt %d/%d), retrying: %s",
                session_id,
                attempt,
                ADK_STREAM_MAX_RETRIES,
                exc,
            )
            await _send_json(
                websocket,
                {
                    "type": "assistant_state",
                    "data": {"state": "reconnecting", "reason": "stream_error", "attempt": attempt},
                },
            )
            await asyncio.sleep(ADK_STREAM_RETRY_BACKOFF_S * attempt)


async def _forward_to_client(
    websocket: WebSocket,
    adk_runner: Runner,
    live_queue: LiveRequestQueue,
    session_id: str = "",
    runtime_state: dict | None = None,
    topic_queue: asyncio.Queue | None = None,
    report: "SessionReport | None" = None,
) -> None:
    """
    Receive ADK Runner events from Gemini and forward them to the browser.

    Runs until the Runner stream ends, the WebSocket disconnects,
    or an unrecoverable error occurs.
    """
    audio_response_chunks = 0
    turn_count = 0
    turn_had_output = False
    drop_turn_in_progress = False

    try:
        runtime_state = runtime_state or {}
        user_id = runtime_state.get("student_id", "unknown")

        async for event in _iter_runner_events_with_retry(
            websocket=websocket,
            adk_runner=adk_runner,
            user_id=user_id,
            session_id=session_id,
            live_queue=live_queue,
            report=report,
        ):
            # Whiteboard notes are dispatched by the whiteboard_dispatcher task
            # (modules/whiteboard.py) which handles speech-sync timing and dedupe.

            # Drain any pending topic updates queued by switch_topic tool
            if topic_queue is not None:
                while not topic_queue.empty():
                    try:
                        update = topic_queue.get_nowait()
                        runtime_state["topic_id"] = update["topic_id"]
                        runtime_state["topic_title"] = update["topic_title"]
                        await _send_json(websocket, {"type": "topic_update", "data": update})
                    except asyncio.QueueEmpty:
                        break

            dc = _debug_counters.get(session_id)
            if dc is not None:
                dc["last_gemini_event_at"] = time.time()

            # If a turn was gated out due missing student/proactive trigger, discard
            # all events until the matching turn_complete boundary.
            if drop_turn_in_progress:
                if event.turn_complete:
                    drop_turn_in_progress = False
                    runtime_state["assistant_speaking"] = False
                    audio_response_chunks = 0
                    turn_had_output = False
                    _debug_logger.debug("TURN_DROPPED_COMPLETE sid=%s (no ticket)", session_id[:8])
                continue

            # --- Audio and text from content parts ---
            if event.content and event.content.parts:
                # Greeting gate: after first greeting turn, suppress output until student speaks.
                # Prevents Gemini from producing duplicate greetings from [SESSION START].
                if runtime_state.get("_greeting_delivered") and not runtime_state.get("_student_has_spoken"):
                    continue

                # General turn gate: allow only one tutor turn per student/proactive trigger.
                if int(runtime_state.get("_turn_ticket_count", 0)) <= 0:
                    runtime_state["assistant_speaking"] = False
                    if event.turn_complete:
                        audio_response_chunks = 0
                        turn_had_output = False
                        _debug_logger.debug("TURN_DROPPED_ONE_EVENT sid=%s (no ticket)", session_id[:8])
                    else:
                        drop_turn_in_progress = True
                        _debug_logger.debug("TURN_DROPPED_START sid=%s (no ticket)", session_id[:8])
                    logger.info("Suppressed extra tutor turn without new student activity")
                    continue

                for part in event.content.parts:
                    # Audio data
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None and inline_data.data:
                        _mark_proactive_response_seen(runtime_state, source="audio")
                        was_speaking = runtime_state.get("assistant_speaking")
                        runtime_state["last_user_activity_at"] = time.time()
                        runtime_state["idle_stage"] = 0
                        runtime_state["assistant_speaking"] = True
                        runtime_state["conversation_started"] = True
                        runtime_state["mic_kickoff_sent"] = True
                        if not was_speaking:
                            reset_silence_tracking(runtime_state)
                            _debug_logger.debug(
                                "SPEAKING_START sid=%s (was silent, now audio)",
                                session_id[:8],
                            )
                            now_audio_out = time.time()
                            ref_time = float(runtime_state.get("latency_last_student_transcript_at", 0.0))
                            if ref_time > 0 and ((now_audio_out - ref_time) * 1000) > RESPONSE_REF_MAX_AGE_MS:
                                ref_time = 0.0
                            if ref_time <= 0:
                                ref_time = float(runtime_state.get("latency_last_audio_in_at", 0.0))
                            if ref_time > 0:
                                response_ms = (now_audio_out - ref_time) * 1000
                                latency_event = record_latency_metric(
                                    runtime_state,
                                    "response_start",
                                    response_ms,
                                )
                                if latency_event:
                                    await _send_json(
                                        websocket,
                                        {"type": "latency_event", "data": latency_event},
                                    )
                                    if report:
                                        report.record_latency_event(
                                            latency_event.get("metric", ""),
                                            latency_event.get("value_ms", 0),
                                            bool(latency_event.get("is_alert", False)),
                                        )
                            if not runtime_state.get("latency_first_byte_recorded", False):
                                runtime_state["latency_first_byte_recorded"] = True
                                runtime_state["latency_first_audio_out_at"] = now_audio_out
                                first_ref = float(runtime_state.get("latency_first_request_at", 0.0))
                                if first_ref <= 0:
                                    first_ref = float(runtime_state.get("latency_last_audio_in_at", 0.0))
                                if first_ref <= 0:
                                    first_ref = float(runtime_state.get("latency_session_start_at", 0.0))
                                if first_ref <= 0:
                                    first_ref = now_audio_out
                                first_byte_ms = (now_audio_out - first_ref) * 1000
                                first_byte_event = record_latency_metric(
                                    runtime_state,
                                    "first_byte",
                                    first_byte_ms,
                                )
                                if first_byte_event:
                                    await _send_json(
                                        websocket,
                                        {"type": "latency_event", "data": first_byte_event},
                                    )
                                    if report:
                                        report.record_latency_event(
                                            first_byte_event.get("metric", ""),
                                            first_byte_event.get("value_ms", 0),
                                            bool(first_byte_event.get("is_alert", False)),
                                        )
                        lat = _latency_state.get(session_id)
                        if lat and lat["awaiting_first_response"] and lat["last_audio_in"] > 0:
                            delta_ms = (time.time() - lat["last_audio_in"]) * 1000
                            logger.info(
                                "LATENCY session=%s response_start_ms=%.0f",
                                session_id, delta_ms,
                            )
                            lat["awaiting_first_response"] = False
                            if report:
                                report.record_first_response_latency(delta_ms)
                        if dc is not None:
                            dc["audio_out"] += 1
                            if dc["audio_out"] == 1:
                                logger.info(
                                    "AUDIO_DEBUG session=%s mime_type=%s data_len=%d first_bytes=%s",
                                    session_id, getattr(inline_data, "mime_type", "NONE"),
                                    len(inline_data.data), inline_data.data[:16].hex(),
                                )
                        runtime_state["_last_tutor_audio_at"] = time.time()
                        audio_bytes: bytes = inline_data.data
                        encoded = base64.b64encode(audio_bytes).decode("utf-8")
                        await _send_json(websocket, {"type": "audio", "data": encoded})
                        turn_had_output = True
                        audio_response_chunks += 1
                        if report:
                            report.record_audio_out()
                        continue

                    # Text data
                    text = getattr(part, "text", None)
                    if text:
                        _mark_proactive_response_seen(runtime_state, source="text")
                        cleaned, had_leak = sanitize_tutor_output(text)
                        if had_leak:
                            logger.info("Sanitized leaked internal text from tutor output")
                        if not cleaned:
                            continue
                        append_tutor_text_part(runtime_state, cleaned, source="text")
                        logger.info("TUTOR TEXT: %s", cleaned)
                        runtime_state["last_user_activity_at"] = time.time()
                        runtime_state["idle_stage"] = 0
                        runtime_state["assistant_speaking"] = True
                        runtime_state["conversation_started"] = True
                        runtime_state["mic_kickoff_sent"] = True
                        reset_silence_tracking(runtime_state)
                        if dc is not None:
                            dc["text_out"] += 1
                        _debug_logger.debug(
                            "TEXT sid=%s data=%s",
                            session_id[:8], str(cleaned)[:120],
                        )
                        await _send_json(websocket, {"type": "text", "data": cleaned})
                        inline_citations = extract_inline_url_citations(
                            cleaned,
                            runtime_state.get("last_forced_search_query", ""),
                        )
                        if inline_citations:
                            seen_urls = runtime_state.setdefault("grounding_seen_urls", set())
                            for cit in inline_citations[:1]:
                                url = str(cit.get("url", "")).strip()
                                if url and url in seen_urls:
                                    continue
                                if url:
                                    seen_urls.add(url)
                                runtime_state["grounding_events"] = runtime_state.get("grounding_events", 0) + 1
                                runtime_state["grounding_citations_sent"] = runtime_state.get("grounding_citations_sent", 0) + 1
                                query = cit.get("query", "")
                                if query:
                                    queries_list = runtime_state.get("grounding_search_queries", [])
                                    queries_list.append(query)
                                    runtime_state["grounding_search_queries"] = queries_list
                                await _send_json(websocket, {"type": "grounding", "data": cit})
                                if report:
                                    report.record_grounding_citation(cit.get("source", ""), query)
                                break
                        turn_had_output = True

            # --- Turn complete ---
            if event.turn_complete:
                # Greeting gate: after first turn, suppress subsequent turns until student speaks
                if runtime_state.get("_greeting_delivered") and not runtime_state.get("_student_has_spoken"):
                    runtime_state["assistant_speaking"] = False
                    _debug_logger.debug("TURN_COMPLETE_SUPPRESSED sid=%s (greeting gate)", session_id[:8])
                    logger.info("Suppressed duplicate greeting turn (turn #%d)", turn_count + 1)
                    audio_response_chunks = 0
                    continue

                # Ignore no-output synthetic turn_complete bursts when no ticket is available.
                if int(runtime_state.get("_turn_ticket_count", 0)) <= 0 and not turn_had_output:
                    _debug_logger.debug("TURN_COMPLETE_SUPPRESSED sid=%s (no ticket/no output)", session_id[:8])
                    continue

                turn_count += 1
                runtime_state["assistant_speaking"] = False
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                reset_silence_tracking(runtime_state)
                if dc is not None:
                    dc["turn_complete"] += 1
                _debug_logger.debug("TURN_COMPLETE sid=%s", session_id[:8])
                await _send_json(websocket, {"type": "turn_complete"})
                logger.info(
                    "Turn #%d complete — sent %d audio chunks to browser",
                    turn_count, audio_response_chunks,
                )
                if report:
                    report.record_turn_complete(audio_response_chunks)
                runtime_state["latency_last_turn_complete_at"] = time.time()
                latency_report = build_latency_report(runtime_state, turns=turn_count)
                await _send_json(websocket, {"type": "latency_report", "data": latency_report})
                if report:
                    report.record_latency_report(latency_report)
                audio_response_chunks = 0
                completed_with_output = turn_had_output
                if turn_had_output and int(runtime_state.get("_turn_ticket_count", 0)) > 0:
                    runtime_state["_turn_ticket_count"] = int(runtime_state.get("_turn_ticket_count", 0)) - 1
                turn_had_output = False
                lang_turn = finalize_language_tutor_turn(runtime_state)
                if lang_turn.get("events"):
                    for language_event in lang_turn["events"]:
                        await _send_json(
                            websocket,
                            {"type": "language_event", "data": language_event},
                        )
                        if report:
                            report.record_language_event(language_event)
                control_prompt = lang_turn.get("control_prompt")
                if control_prompt:
                    live_queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=control_prompt)])
                    )
                language_metric = build_language_metric_snapshot(runtime_state)
                await _send_json(
                    websocket,
                    {
                        "type": "language_metric",
                        "data": language_metric,
                    },
                )
                if report:
                    report.record_language_metric(language_metric)

                # Track whether the tutor is waiting on a student reply and detect
                # near-duplicate prompt loops when no new student activity happened.
                turn_text = str(lang_turn.get("turn_text") or "").strip()
                if completed_with_output and turn_text:
                    runtime_state["awaiting_student_reply"] = expects_student_reply(turn_text)
                    if report and runtime_state["awaiting_student_reply"]:
                        report.record_awaiting_reply_prompt()
                    activity_count = int(runtime_state.get("student_activity_count", 0))
                    last_turn_text = str(runtime_state.get("last_tutor_prompt_text") or "")
                    last_activity_count = int(runtime_state.get("last_tutor_prompt_activity_count", -1))
                    if (
                        last_turn_text
                        and activity_count == last_activity_count
                        and is_near_duplicate(last_turn_text, turn_text)
                    ):
                        runtime_state["suspected_repetition_count"] = int(
                            runtime_state.get("suspected_repetition_count", 0)
                        ) + 1
                        if report:
                            report.record_repetition_suspected()
                        logger.warning(
                            "Session %s: suspected repetitive tutor prompt loop (count=%d)",
                            session_id,
                            runtime_state["suspected_repetition_count"],
                        )
                        # Nudge the model away from repeating the same prompt pattern.
                        now = time.time()
                        if (now - float(runtime_state.get("last_hidden_prompt_at", 0.0))) >= 2.0:
                            live_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part(text=ANTI_REPEAT_CONTROL_PROMPT)],
                                )
                            )
                            runtime_state["last_hidden_prompt_at"] = now

                    if is_question_like_turn(turn_text):
                        runtime_state["question_like_streak"] = int(
                            runtime_state.get("question_like_streak", 0)
                        ) + 1
                    else:
                        runtime_state["question_like_streak"] = 0

                    if runtime_state.get("question_like_streak", 0) >= 3:
                        now = time.time()
                        if (now - float(runtime_state.get("last_hidden_prompt_at", 0.0))) >= 2.0:
                            live_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part(text=ANTI_QUESTION_LOOP_CONTROL_PROMPT)],
                                )
                            )
                            runtime_state["last_hidden_prompt_at"] = now
                    runtime_state["last_tutor_prompt_text"] = turn_text
                    runtime_state["last_tutor_prompt_activity_count"] = activity_count
                else:
                    runtime_state["awaiting_student_reply"] = False

                # Mark greeting as delivered only after a turn with actual audio/text.
                # Silent/empty turns (e.g. model acknowledging [SESSION START]) must
                # not lock the gate — the real audio greeting arrives in a later turn.
                if not runtime_state.get("_greeting_delivered") and completed_with_output:
                    runtime_state["_greeting_delivered"] = True

            # --- Interrupted ---
            if event.interrupted:
                # Stale interrupt filter: if assistant already stopped speaking
                # and no audio chunks were sent this turn, skip forwarding to client.
                if not runtime_state.get("assistant_speaking") and audio_response_chunks == 0:
                    _debug_logger.debug(
                        "INTERRUPTED_STALE sid=%s (already silent, 0 chunks)", session_id[:8],
                    )
                    if report:
                        report.record_stale_interruption()
                    continue
                runtime_state["assistant_speaking"] = False
                _mark_student_activity(runtime_state, unlock_turn=True)
                if dc is not None:
                    dc["interrupted"] += 1
                _debug_logger.debug("INTERRUPTED sid=%s", session_id[:8])
                if report:
                    report.record_interruption()
                lat = _latency_state.get(session_id)
                if lat and lat["last_audio_in"] > 0:
                    delta_ms = (time.time() - lat["last_audio_in"]) * 1000
                    logger.info(
                        "LATENCY session=%s interruption_stop_ms=%.0f",
                        session_id, delta_ms,
                    )
                    lat["awaiting_first_response"] = False
                barge_in_at = float(runtime_state.get("latency_last_barge_in_at", 0.0))
                if barge_in_at > 0:
                    interruption_stop_ms = (time.time() - barge_in_at) * 1000
                    runtime_state["latency_last_barge_in_at"] = 0.0
                    latency_event = record_latency_metric(
                        runtime_state,
                        "interruption_stop",
                        interruption_stop_ms,
                    )
                    if latency_event:
                        await _send_json(
                            websocket,
                            {"type": "latency_event", "data": latency_event},
                        )
                        if report:
                            report.record_latency_event(
                                latency_event.get("metric", ""),
                                latency_event.get("value_ms", 0),
                                bool(latency_event.get("is_alert", False)),
                            )
                await _send_json(websocket, {"type": "interrupted"})
                logger.info(
                    "INTERRUPTED by student (had sent %d audio chunks before interruption)",
                    audio_response_chunks,
                )
                audio_response_chunks = 0

            # --- Grounding metadata ---
            grounding_citations = extract_grounding(event)
            if grounding_citations:
                seen_urls = runtime_state.setdefault("grounding_seen_urls", set())
                for cit in grounding_citations[:1]:  # Only top citation
                    url = str(cit.get("url", "")).strip()
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    runtime_state["grounding_events"] = runtime_state.get("grounding_events", 0) + 1
                    runtime_state["grounding_citations_sent"] = runtime_state.get("grounding_citations_sent", 0) + 1
                    query = cit.get("query", "")
                    if query:
                        queries_list = runtime_state.get("grounding_search_queries", [])
                        queries_list.append(query)
                        runtime_state["grounding_search_queries"] = queries_list
                    logger.info(
                        "GROUNDING #%d: %s (%s)",
                        runtime_state["grounding_citations_sent"],
                        cit["snippet"][:80],
                        cit["source"],
                    )
                    await _send_json(websocket, {"type": "grounding", "data": cit})
                    if report:
                        report.record_grounding_citation(cit["source"], cit.get("query", ""))

            # --- Transcription ---
            if event.input_transcription and event.input_transcription.text and event.input_transcription.finished:
                student_text = event.input_transcription.text
                logger.info("STUDENT TRANSCRIPT: %s", student_text)
                # Echo guard: while tutor output is active, transcription often captures
                # assistant audio from speakers. In that case, do not unlock another turn.
                now = time.time()
                is_echo_window = (
                    runtime_state.get("assistant_speaking")
                    or (now - float(runtime_state.get("_last_tutor_audio_at", 0.0))) < 0.8
                )
                if is_echo_window:
                    _debug_logger.debug("INPUT_TRANSCRIPT_IGNORED sid=%s (echo-window)", session_id[:8])
                    continue

                _mark_student_activity(runtime_state, unlock_turn=True)
                runtime_state["_student_has_spoken"] = True
                runtime_state["latency_last_student_transcript_at"] = now
                if float(runtime_state.get("latency_first_request_at", 0.0)) <= 0:
                    runtime_state["latency_first_request_at"] = now
                last_turn_complete_at = float(runtime_state.get("latency_last_turn_complete_at", 0.0))
                if last_turn_complete_at > 0:
                    turn_gap_ms = (now - last_turn_complete_at) * 1000
                    if turn_gap_ms <= TURN_TO_TURN_MAX_GAP_MS:
                        turn_to_turn_event = record_latency_metric(
                            runtime_state,
                            "turn_to_turn",
                            turn_gap_ms,
                        )
                        if turn_to_turn_event:
                            await _send_json(
                                websocket,
                                {"type": "latency_event", "data": turn_to_turn_event},
                            )
                            if report:
                                report.record_latency_event(
                                    turn_to_turn_event.get("metric", ""),
                                    turn_to_turn_event.get("value_ms", 0),
                                    bool(turn_to_turn_event.get("is_alert", False)),
                                )

                # Force search-grounding behavior for explicit educational search requests.
                if detect_explicit_search_request(student_text, runtime_state) and is_likely_educational_search(student_text, runtime_state):
                    search_query = extract_search_query(student_text)
                    last_query = str(runtime_state.get("last_forced_search_query") or "")
                    last_forced_at = float(runtime_state.get("last_forced_search_at", 0.0))
                    should_force = search_query and (
                        search_query.lower() != last_query.lower() or (now - last_forced_at) > 20.0
                    )
                    if should_force:
                        forced_prompt = build_force_search_control_prompt(search_query)
                        live_queue.send_content(
                            types.Content(role="user", parts=[types.Part(text=forced_prompt)])
                        )
                        runtime_state["last_forced_search_query"] = search_query
                        runtime_state["last_forced_search_at"] = now
                        runtime_state["last_hidden_prompt_at"] = now

                language_update = handle_language_student_transcript(
                    student_text,
                    runtime_state,
                )
                for language_event in language_update.get("events", []):
                    await _send_json(
                        websocket,
                        {"type": "language_event", "data": language_event},
                    )
                    if report:
                        report.record_language_event(language_event)
                control_prompt = language_update.get("control_prompt")
                if control_prompt:
                    live_queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=control_prompt)])
                    )

                # Guardrail: check student input for off-topic / cheat / inappropriate
                student_guardrail_events = check_student_input(student_text)
                for ge in student_guardrail_events:
                    record_guardrail_event(runtime_state, ge, source="student_speech")
                    await _send_json(websocket, {
                        "type": "guardrail_event",
                        "data": {
                            "type": ge["guardrail"],
                            "source": "student_speech",
                            "detail": ge["detail"],
                        },
                    })
                    if report:
                        report.record_guardrail_event(ge["guardrail"], ge["severity"], "student_speech")
                # Inject reinforcement if guardrail triggered
                reinforce_prompt = select_reinforcement(student_guardrail_events, runtime_state)
                if reinforce_prompt:
                    reason = student_guardrail_events[0]["guardrail"] if student_guardrail_events else "unknown"
                    live_queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=reinforce_prompt)])
                    )
                    record_reinforcement(runtime_state, reason)
                    if report:
                        report.record_guardrail_reinforcement()

                if report:
                    report.record_student_transcript(student_text)

            if event.output_transcription and event.output_transcription.text and event.output_transcription.finished:
                tutor_text_raw = event.output_transcription.text
                tutor_text, had_internal = sanitize_tutor_output(tutor_text_raw)
                if had_internal:
                    logger.info("Sanitized leaked internal text from tutor transcript")
                if not tutor_text:
                    continue

                logger.info("TUTOR TRANSCRIPT: %s", tutor_text)
                append_tutor_text_part(runtime_state, tutor_text, source="transcript")

                # Guardrail: check tutor output for answer leaks
                tutor_guardrail_events = check_tutor_output(tutor_text, runtime_state)
                for ge in tutor_guardrail_events:
                    record_guardrail_event(runtime_state, ge, source="tutor_speech")
                    await _send_json(websocket, {
                        "type": "guardrail_event",
                        "data": {
                            "type": ge["guardrail"],
                            "source": "tutor_speech",
                            "detail": ge["detail"],
                        },
                    })
                    if report:
                        report.record_guardrail_event(ge["guardrail"], ge["severity"], "tutor_speech")
                # Inject Socratic reinforcement if answer leak detected
                reinforce_prompt = select_reinforcement(tutor_guardrail_events, runtime_state)
                if reinforce_prompt:
                    live_queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=reinforce_prompt)])
                    )
                    record_reinforcement(runtime_state, "answer_leak")
                    if report:
                        report.record_guardrail_reinforcement()

                if report:
                    report.record_tutor_transcript(tutor_text)

    except WebSocketDisconnect:
        logger.info("Browser disconnected (forward_to_client)")
    except Exception as exc:
        logger.exception("Unexpected error in forward_to_client: %s", exc)
        if report:
            report.record_error(f"forward_to_client: {exc}")
        await _send_json(websocket, {
            "type": "error",
            "data": "The tutor connection was interrupted. Please refresh to start a new session.",
        })


async def _send_json(websocket: WebSocket, payload: dict) -> None:
    """Send a JSON payload to the browser, ignoring errors on a closed socket."""
    try:
        await websocket.send_text(json.dumps(payload))
    except (RuntimeError, WebSocketDisconnect):
        logger.debug(
            "Could not send '%s' to browser (socket closed)",
            payload.get("type"),
        )
    except Exception:
        logger.warning(
            "Unexpected error sending '%s' to browser",
            payload.get("type"),
            exc_info=True,
        )
