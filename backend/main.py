"""
SeeMe Tutor — FastAPI backend.

Bridges browser WebSocket connections to the Gemini Live API.
Audio and video frames flow from the browser to Gemini; audio responses
and text transcripts flow back to the browser.
"""

import asyncio
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
)
from modules.screen_share import init_screen_share_state
from modules.whiteboard import (
    init_whiteboard_state,
    whiteboard_dispatcher,
)
from modules.guardrails import init_guardrails_state
from modules.grounding import init_grounding_state
from modules.latency import (
    init_latency_state,
    format_latency_summary,
)
from modules.live_session import (
    build_live_run_config,
    load_latest_resumption_handle,
    save_resumption_handle,
)
from modules.prompt_capture import (
    capture_prompt_text,
    send_content_with_prompt_capture,
)
from modules.memory_manager import (
    build_hidden_memory_context,
    init_memory_state,
)
from modules.security import (
    SlidingWindowRateLimiter,
    build_security_headers,
    extract_client_ip,
    parse_allowed_origins,
)
from modules.tutor_preferences import (
    _SEARCH_REQUEST_PATTERNS_BY_LANG,
    _SEARCH_EDU_HINT_PATTERNS_BY_LANG,
    _SEARCH_NON_EDU_PATTERNS,
    _TUTOR_PREFERENCE_OPTIONS,
    _DEFAULT_TUTOR_PREFERENCES,
    _PROFILE_CONTEXT_MAX_LEN,
    _PROFILE_CONTEXT_FIELDS,
    _RESOURCE_MATERIAL_MAX_ITEMS,
    _PLAN_MILESTONE_MIN_DEFAULT,
    _normalize_preference_choice,
    _normalize_tutor_preferences,
    _sanitize_text,
    _sanitize_long_text,
    _normalize_resource_materials,
    _agent_phase_from_session_phase,
    _normalize_profile_context,
    _build_tutor_preferences_control_prompt,
    _dedupe_patterns,
    _all_patterns,
)
from modules.student_profile import (
    _anonymize_ip,
    _is_adk_session_exists_error,
    _parse_int,
    _safe_order_index,
    _default_backlog_context,
    _register_active_student_session as _register_active_student_session_impl,
    _unregister_active_student_session as _unregister_active_student_session_impl,
    _load_backlog_context as _load_backlog_context_impl,
    _build_profile_summary,
    _init_local_profiles as _init_local_profiles_impl,
    _build_local_profile_summary as _build_local_profile_summary_impl,
)
from modules.http_routes import router as api_router, http_security_middleware
from modules.session_helpers import (
    _session_heartbeat,
    _session_timer,
    _merge_resume_message,
)
from modules.persistence import (
    _load_memory_recall,
    _persist_memory_checkpoint,
    _persist_session_metrics_summary,
)
from modules.ws_bridge import (
    _forward_to_gemini,
    _forward_to_client,
    _send_json,
    _StudentEndedSession,
)
from seed_demo_profiles import PROFILES as _SEED_PROFILES

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
if not any(
    isinstance(handler, RotatingFileHandler)
    and getattr(handler, "baseFilename", "") == _debug_fh.baseFilename
    for handler in _debug_logger.handlers
):
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
ADK_STREAM_MAX_RETRIES = 3
ADK_STREAM_RETRY_BACKOFF_S = 0.6
TURN_TO_TURN_MAX_GAP_MS = 3000
RESPONSE_REF_MAX_AGE_MS = 2500
LIVE_COMPRESSION_ENABLED = True
LIVE_COMPRESSION_TRIGGER_TOKENS = 32000
LIVE_COMPRESSION_TARGET_TOKENS = 16000
MEMORY_CHECKPOINT_INTERVAL_S = 300
MEMORY_RECALL_BUDGET_TOKENS = 500
MEMORY_RECALL_MAX_CELLS = 6
MEMORY_CHECKPOINT_MAX_AGE_S = 24 * 60 * 60

# Per-session latency tracking: session_id -> {"last_audio_in": float, "awaiting_first_response": bool}
_latency_state: dict[str, dict] = {}
# Per-session debug counters: session_id -> {audio_in: int, audio_out: int, ...}
_debug_counters: dict[str, dict] = {}
_active_student_sessions: dict[str, dict] = {}
_active_student_sessions_lock = asyncio.Lock()

_STUDENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SESSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,127}$")


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


def _env_float(name: str, default: float, *, minimum: float = 0.0, maximum: float = 10000.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


ADK_STREAM_MAX_RETRIES = _env_int("ADK_STREAM_MAX_RETRIES", ADK_STREAM_MAX_RETRIES, minimum=1, maximum=8)
ADK_STREAM_RETRY_BACKOFF_S = _env_float(
    "ADK_STREAM_RETRY_BACKOFF_S",
    ADK_STREAM_RETRY_BACKOFF_S,
    minimum=0.1,
    maximum=5.0,
)
LIVE_COMPRESSION_ENABLED = _env_bool("LIVE_COMPRESSION_ENABLED", LIVE_COMPRESSION_ENABLED)
LIVE_COMPRESSION_TRIGGER_TOKENS = _env_int(
    "LIVE_COMPRESSION_TRIGGER_TOKENS",
    LIVE_COMPRESSION_TRIGGER_TOKENS,
    minimum=2048,
    maximum=512000,
)
LIVE_COMPRESSION_TARGET_TOKENS = _env_int(
    "LIVE_COMPRESSION_TARGET_TOKENS",
    LIVE_COMPRESSION_TARGET_TOKENS,
    minimum=1024,
    maximum=512000,
)
MEMORY_CHECKPOINT_INTERVAL_S = _env_int(
    "MEMORY_CHECKPOINT_INTERVAL_S",
    MEMORY_CHECKPOINT_INTERVAL_S,
    minimum=60,
    maximum=3600,
)
MEMORY_RECALL_BUDGET_TOKENS = _env_int(
    "MEMORY_RECALL_BUDGET_TOKENS",
    MEMORY_RECALL_BUDGET_TOKENS,
    minimum=120,
    maximum=8000,
)
MEMORY_RECALL_MAX_CELLS = _env_int(
    "MEMORY_RECALL_MAX_CELLS",
    MEMORY_RECALL_MAX_CELLS,
    minimum=1,
    maximum=20,
)
if LIVE_COMPRESSION_TARGET_TOKENS >= LIVE_COMPRESSION_TRIGGER_TOKENS:
    LIVE_COMPRESSION_TARGET_TOKENS = max(1024, int(LIVE_COMPRESSION_TRIGGER_TOKENS * 0.75))


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



# _anonymize_ip, _is_adk_session_exists_error, _parse_int, _safe_order_index
# moved to modules.student_profile


# _append_tutor_turn_part, _finalize_tutor_turn moved to modules.session_helpers

# _default_backlog_context, _register_active_student_session, _unregister_active_student_session
# moved to modules.student_profile


async def _register_active_student_session(student_id: str, session_id: str, websocket: WebSocket) -> tuple[str | None, WebSocket | None]:
    """Thin wrapper — delegates to student_profile module with main.py globals."""
    return await _register_active_student_session_impl(
        _active_student_sessions, _active_student_sessions_lock,
        student_id, session_id, websocket,
    )


async def _unregister_active_student_session(student_id: str, session_id: str) -> None:
    """Thin wrapper — delegates to student_profile module with main.py globals."""
    await _unregister_active_student_session_impl(
        _active_student_sessions, _active_student_sessions_lock,
        student_id, session_id,
    )


async def _load_backlog_context(student_id: str, session_data: dict | None = None) -> dict | None:
    """Thin wrapper — delegates to student_profile module with firestore_client."""
    return await _load_backlog_context_impl(firestore_client, student_id, session_data)


# _build_profile_summary moved to modules.student_profile (imported directly)

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
# In-memory profile store — local dev fallback when Firestore is unavailable.
# Populated from seed_demo_profiles.PROFILES on startup.
# ---------------------------------------------------------------------------
_local_profiles: dict[str, dict] = {}  # student_id -> full student doc
_local_tracks: dict[str, list[dict]] = {}  # student_id -> list of track dicts
_local_topics: dict[str, dict[str, list[dict]]] = {}  # student_id -> {track_id: [topic dicts]}
_local_sessions: dict[str, dict] = {}  # session_id -> session doc


def _build_local_profile_summary(student_id: str) -> dict | None:
    """Thin wrapper — delegates to student_profile module with local store dicts."""
    return _build_local_profile_summary_impl(student_id, _local_profiles, _local_tracks, _local_topics)


_init_local_profiles_impl(_local_profiles, _local_tracks, _local_topics, _SEED_PROFILES)

# ---------------------------------------------------------------------------
# ADK Runner + Session Service
# ---------------------------------------------------------------------------
session_service = InMemorySessionService()
runner = Runner(
    app_name="seeme_tutor",
    agent=tutor_agent,
    session_service=session_service,
)

ADK_BASE_RUN_CONFIG = RunConfig(
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
            start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
            end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=300,
            silence_duration_ms=700,
        ),
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
)


def _build_session_run_config(resumption_handle: str | None = None) -> tuple[RunConfig, dict]:
    """Build per-session RunConfig with optional compression + resumption."""
    run_config, meta = build_live_run_config(
        ADK_BASE_RUN_CONFIG,
        types,
        compression_enabled=LIVE_COMPRESSION_ENABLED,
        compression_trigger_tokens=LIVE_COMPRESSION_TRIGGER_TOKENS,
        compression_target_tokens=LIVE_COMPRESSION_TARGET_TOKENS,
        resumption_handle=resumption_handle,
    )
    return run_config, meta

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

# ---------------------------------------------------------------------------
# Expose shared state via app.state for use by http_routes module
# ---------------------------------------------------------------------------
app.state.firestore_client = firestore_client
app.state.http_rate_limiter = _http_rate_limiter
app.state.http_rate_limit_max = HTTP_RATE_LIMIT_MAX
app.state.http_rate_limit_window_s = HTTP_RATE_LIMIT_WINDOW_S
app.state.security_headers = SECURITY_HEADERS
app.state.frontend_dir = FRONTEND_DIR
app.state.local_profiles = _local_profiles
app.state.local_tracks = _local_tracks
app.state.local_topics = _local_topics
app.state.local_sessions = _local_sessions
app.state.active_student_sessions = _active_student_sessions
app.state.active_student_sessions_lock = _active_student_sessions_lock

# ---------------------------------------------------------------------------
# Register HTTP middleware and REST routes (extracted to modules.http_routes)
# ---------------------------------------------------------------------------
app.middleware("http")(http_security_middleware)
app.include_router(api_router)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    logger.info("Serving frontend static files from %s", FRONTEND_DIR)
else:
    logger.warning("Frontend directory not found at %s — static serving disabled", FRONTEND_DIR)


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
    if not firestore_client:
        await _send_json(
            websocket,
            {"type": "error", "data": "Firestore is required in V1. Configure backend storage first."},
        )
        await websocket.close(code=1013)
        return

    raw_session_id = websocket.query_params.get("session_id", "").strip().lower()
    if not raw_session_id:
        await _send_json(
            websocket,
            {"type": "error", "data": "Please select or create a session before starting."},
        )
        await websocket.close(code=1008)
        return
    if not _SESSION_ID_PATTERN.match(raw_session_id):
        await _send_json(
            websocket,
            {"type": "error", "data": "Invalid session identifier. Please reselect the session."},
        )
        await websocket.close(code=1008)
        return

    session_snapshot = await firestore_client.collection("sessions").document(raw_session_id).get() if firestore_client else None
    if not session_snapshot or not session_snapshot.exists:
        await _send_json(
            websocket,
            {"type": "error", "data": "Session not found. Please create a new session."},
        )
        await websocket.close(code=1008)
        return
    selected_session = session_snapshot.to_dict() or {}
    session_student = str(selected_session.get("student_id") or "").strip().lower()
    if session_student != raw_student_id:
        await _send_json(
            websocket,
            {"type": "error", "data": "This session does not belong to the selected profile."},
        )
        await websocket.close(code=1008)
        return
    if str(selected_session.get("status") or "open").strip().lower() == "mastered":
        await _send_json(
            websocket,
            {"type": "error", "data": "This topic is already mastered. Create a new session for a new topic."},
        )
        await websocket.close(code=1008)
        return

    backlog_context = await _load_backlog_context(raw_student_id, session_data=selected_session)
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
    memory_recall = await _load_memory_recall(
        raw_student_id,
        str(backlog_context.get("topic_id") or ""),
        firestore_client,
        recall_budget_tokens=MEMORY_RECALL_BUDGET_TOKENS,
        recall_max_cells=MEMORY_RECALL_MAX_CELLS,
        checkpoint_max_age_s=MEMORY_CHECKPOINT_MAX_AGE_S,
    )
    memory_recall_available = bool(
        isinstance(memory_recall, dict)
        and (
            int(memory_recall.get("selected_count", 0)) > 0
            or str(memory_recall.get("summary") or "").strip()
        )
    )
    if memory_recall_available:
        backlog_context["memory_recall"] = {
            "candidate_count": int(memory_recall.get("candidate_count", 0)),
            "selected_count": int(memory_recall.get("selected_count", 0)),
            "token_estimate": int(memory_recall.get("token_estimate", 0)),
            "budget_utilization_percent": float(memory_recall.get("budget_utilization_percent", 0.0)),
            "summary": str(memory_recall.get("summary") or ""),
        }
        backlog_context["resume_message"] = _merge_resume_message(
            str(backlog_context.get("resume_message") or ""),
            str(memory_recall.get("summary") or ""),
        )

    resumption_handle = ""
    session_run_config, run_config_meta = _build_session_run_config(
        resumption_handle=resumption_handle or None
    )

    session_id = raw_session_id
    session_start = float(selected_session.get("started_at") or time.time())
    logger.info("Session %s accepted from %s", session_id, client_host)

    # Update session activity
    if firestore_client:
        try:
            await firestore_client.collection("sessions").document(session_id).set({
                "session_id": session_id,
                "client_host": _anonymize_ip(client_host),
                "status": "open",
                "ended_reason": None,
                "closed_at": None,
                "student_id": raw_student_id,
                "track_id": backlog_context.get("track_id"),
                "track_title": backlog_context.get("track_title"),
                "topic_id": backlog_context.get("topic_id"),
                "topic_title": backlog_context.get("topic_title"),
                "phase": backlog_context.get("session_phase") or selected_session.get("phase") or "setup",
                "updated_at": time.time(),
            }, merge=True)
        except Exception:
            logger.warning("Session %s: failed to update session activity", session_id, exc_info=True)

    session_state = {
        "session_id": session_id,
        "gcp_project_id": GCP_PROJECT_ID,
        "student_id": raw_student_id,
        "student_name": backlog_context.get("student_name"),
        "track_id": backlog_context.get("track_id"),
        "track_title": backlog_context.get("track_title"),
        "topic_id": backlog_context.get("topic_id"),
        "topic_title": backlog_context.get("topic_title"),
        "topic_status": backlog_context.get("topic_status"),
        "available_topics": backlog_context.get("available_topics", []),
        "search_intent_policy": backlog_context.get("search_intent_policy"),
        "search_context_terms": backlog_context.get("search_context_terms", []),
        "tutor_preferences": backlog_context.get("tutor_preferences"),
        "profile_context": backlog_context.get("profile_context", {}),
        "session_setup": backlog_context.get("session_setup", {}),
        "plan_bootstrap_required": bool(backlog_context.get("plan_bootstrap_required")),
        "plan_bootstrap_completed": bool(backlog_context.get("plan_bootstrap_completed")),
        "plan_milestone_min": _parse_int(
            backlog_context.get("plan_milestone_min"),
            _PLAN_MILESTONE_MIN_DEFAULT,
            minimum=1,
            maximum=20,
        ),
        "plan_milestone_count": _parse_int(
            backlog_context.get("plan_milestone_count"),
            0,
            minimum=0,
            maximum=50,
        ),
        "plan_fallback_generated": bool(backlog_context.get("plan_fallback_generated")),
        "plan_bootstrap_source": _sanitize_text(
            backlog_context.get("plan_bootstrap_source"),
            max_len=40,
        ),
        "resource_transcript_context": backlog_context.get("resource_transcript_context", ""),
        "resource_transcript_available": bool(backlog_context.get("resource_transcript_available")),
        "previous_notes": backlog_context.get("previous_notes", []),
        "resume_message": backlog_context.get("resume_message"),
        "memory_recall": memory_recall,
        "session_phase": _agent_phase_from_session_phase(
            str(backlog_context.get("session_phase") or selected_session.get("phase") or "setup")
        ),
    }
    report = create_report(session_id, raw_student_id)
    report.record_run_config(
        run_config_meta,
        resumption_requested=False,
    )
    backlog_context_payload = dict(backlog_context)
    resource_transcript_context = str(backlog_context_payload.get("resource_transcript_context") or "").strip()
    if resource_transcript_context:
        backlog_context_payload["resource_transcript_context_available"] = True
        backlog_context_payload.pop("resource_transcript_context", None)
    backlog_context_payload["session_id"] = session_id
    backlog_context_payload["resumption_requested"] = False
    await _send_json(websocket, {"type": "backlog_context", "data": backlog_context_payload})
    report.record_backlog_sent()
    report.record_memory_recall_applied(
        selected_count=int(memory_recall.get("selected_count", 0)),
        token_estimate=int(memory_recall.get("token_estimate", 0)),
        candidate_count=int(memory_recall.get("candidate_count", 0)),
    )
    if memory_recall_available:
        await _send_json(
            websocket,
            {
                "type": "memory_recall",
                "data": {
                    "candidate_count": int(memory_recall.get("candidate_count", 0)),
                    "selected_count": int(memory_recall.get("selected_count", 0)),
                    "token_estimate": int(memory_recall.get("token_estimate", 0)),
                    "budget_utilization_percent": float(memory_recall.get("budget_utilization_percent", 0.0)),
                    "summary": str(memory_recall.get("summary") or ""),
                },
            },
        )
    for note in backlog_context.get("previous_notes", []):
        await _send_json(websocket, {"type": "whiteboard", "data": note})
    if state := selected_session.get("phase"):
        await _send_json(
            websocket,
            {
                "type": "assistant_state",
                "data": {"state": "active", "reason": f"session_phase_{state}"},
            },
        )

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
        "pending_study_question": None,
        "question_note_counter": 0,
        "question_note_signatures": set(),
        "example_note_counter": 0,
        "example_note_signatures": set(),
        "_tutor_turn_parts": {"text": [], "transcript": []},
        "last_forced_search_query": "",
        "last_forced_search_at": 0.0,
        "search_intent_policy": backlog_context.get("search_intent_policy"),
        "search_context_terms": backlog_context.get("search_context_terms", []),
        "tutor_preferences": backlog_context.get("tutor_preferences"),
        "profile_context": backlog_context.get("profile_context", {}),
        "session_setup": backlog_context.get("session_setup", {}),
        "resource_transcript_context": backlog_context.get("resource_transcript_context", ""),
        "resume_message": backlog_context.get("resume_message"),
        "session_id": session_id,
        "student_id": raw_student_id,
        "student_name": backlog_context.get("student_name"),
        "track_id": backlog_context.get("track_id"),
        "session_run_config": session_run_config,
        "session_run_config_meta": run_config_meta,
        "session_resumption_handle": resumption_handle,
        "session_resumption_requested": bool(resumption_handle),
        "session_resumption_active": False,
        "session_resumption_fallback_used": False,
        "live_token_estimate": 0,
        "live_compression_trigger_tokens": LIVE_COMPRESSION_TRIGGER_TOKENS,
        "live_compression_target_tokens": LIVE_COMPRESSION_TARGET_TOKENS,
        "live_compression_last_notified_at": 0.0,
        "memory_recall": memory_recall,
        **init_proactive_state(),
        **init_screen_share_state(),
        **init_whiteboard_state(),
        **init_guardrails_state(),
        **init_grounding_state(),
        **init_latency_state(session_start),
        **init_memory_state(
            checkpoint_interval_s=MEMORY_CHECKPOINT_INTERVAL_S,
            recall_budget_tokens=MEMORY_RECALL_BUDGET_TOKENS,
            recall_max_cells=MEMORY_RECALL_MAX_CELLS,
        ),
        "topic_id": backlog_context.get("topic_id"),
        "topic_title": backlog_context.get("topic_title"),
        "track_title": backlog_context.get("track_title"),
        "topic_context_query": backlog_context.get("topic_context_query", ""),
        "topic_context_summary": backlog_context.get("topic_context_summary", ""),
        "_report": report,
    }
    recall_token_estimate = int(memory_recall.get("token_estimate", 0) or 0)
    if recall_token_estimate > int(runtime_state.get("memory_recall_budget_tokens", MEMORY_RECALL_BUDGET_TOKENS)):
        runtime_state["memory_budget_violations"] = int(runtime_state.get("memory_budget_violations", 0)) + 1
        report.record_memory_budget_violation(
            token_estimate=recall_token_estimate,
            budget_tokens=int(runtime_state.get("memory_recall_budget_tokens", MEMORY_RECALL_BUDGET_TOKENS)),
        )
    wb_queue = register_whiteboard_queue(session_id)
    topic_queue = register_topic_update_queue(session_id)
    if bool(backlog_context.get("plan_bootstrap_required")):
        try:
            wb_queue.put_nowait({"action": "clear"})
            wb_queue.put_nowait({"action": "clear_dedupe"})
        except Exception:
            logger.warning("Session %s: failed to pre-clear whiteboard for plan bootstrap", session_id, exc_info=True)
    ended_reason = "disconnect"
    try:
        try:
            # Create ADK session with initial state
            try:
                await session_service.create_session(
                    app_name="seeme_tutor",
                    user_id=raw_student_id,
                    state=session_state,
                    session_id=session_id,
                )
            except Exception as create_exc:
                if not _is_adk_session_exists_error(create_exc):
                    raise
                logger.warning(
                    "Session %s: ADK session id already exists, recycling in-memory session and retrying create_session",
                    session_id,
                )
                await session_service.delete_session(
                    app_name="seeme_tutor",
                    user_id=raw_student_id,
                    session_id=session_id,
                )
                await session_service.create_session(
                    app_name="seeme_tutor",
                    user_id=raw_student_id,
                    state=session_state,
                    session_id=session_id,
                )

            # Create queue for browser→Gemini upstream
            live_queue = LiveRequestQueue()

            # Persist system instruction prompt snapshot (sent via ADK run config).
            capture_prompt_text(
                SYSTEM_PROMPT,
                session_id=session_id,
                source="system_instruction",
                role="system",
                runtime_state=runtime_state,
            )

            # Send [SESSION START] hidden turn with student context
            student_context = {
                "student_name": session_state.get("student_name"),
                "resume_message": session_state.get("resume_message"),
                "track_title": session_state.get("track_title"),
                "topic_title": session_state.get("topic_title"),
                "topic_status": session_state.get("topic_status"),
                "topic_context_summary": session_state.get("topic_context_summary", ""),
                "tutor_preferences": session_state.get("tutor_preferences"),
                "profile_context": session_state.get("profile_context"),
                "session_setup": session_state.get("session_setup"),
                "resource_transcript_available": bool(
                    session_state.get("resource_transcript_available")
                    or str(session_state.get("resource_transcript_context") or "").strip()
                ),
                "plan_bootstrap_required": bool(
                    session_state.get("plan_bootstrap_required")
                ),
                "plan_bootstrap_completed": bool(
                    session_state.get("plan_bootstrap_completed")
                ),
                "plan_milestone_min": _parse_int(
                    session_state.get("plan_milestone_min"),
                    _PLAN_MILESTONE_MIN_DEFAULT,
                    minimum=1,
                    maximum=20,
                ),
                "plan_milestone_count": _parse_int(
                    session_state.get("plan_milestone_count"),
                    0,
                    minimum=0,
                    maximum=50,
                ),
                "plan_fallback_generated": bool(
                    session_state.get("plan_fallback_generated")
                ),
                "plan_bootstrap_source": _sanitize_text(
                    session_state.get("plan_bootstrap_source"),
                    max_len=40,
                ),
                "search_context_terms": session_state.get("search_context_terms", []),
                "session_phase": session_state.get("session_phase"),
                "previous_notes_count": len(session_state.get("previous_notes", [])),
                "memory_recall_count": int(memory_recall.get("selected_count", 0)),
            }
            import json as _json
            send_content_with_prompt_capture(
                live_queue,
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
                ),
                session_id=session_id,
                source="session_start_context",
                runtime_state=runtime_state,
            )
            memory_context = build_hidden_memory_context(memory_recall)
            if memory_context:
                send_content_with_prompt_capture(
                    live_queue,
                    types.Content(
                        role="user",
                        parts=[types.Part(text=memory_context)],
                    ),
                    session_id=session_id,
                    source="memory_recall_context",
                    runtime_state=runtime_state,
                )
                runtime_state["memory_recall_count"] = int(runtime_state.get("memory_recall_count", 0)) + 1
            resource_transcript_context = str(
                session_state.get("resource_transcript_context") or ""
            ).strip()
            if resource_transcript_context:
                send_content_with_prompt_capture(
                    live_queue,
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=(
                                    "INTERNAL CONTROL: Resource transcript context follows. "
                                    "Treat as background grounding.\n"
                                    f"{resource_transcript_context}\n"
                                    "[IMPORTANT: Use this transcript as grounding context for tutoring in this session. "
                                    "Do not read it aloud verbatim. Ask what the student wants to understand first.]"
                                )
                            )
                        ],
                    ),
                    session_id=session_id,
                    source="resource_transcript_context",
                    runtime_state=runtime_state,
                )
            if bool(session_state.get("plan_bootstrap_required")):
                transcript_available_for_bootstrap = bool(
                    session_state.get("resource_transcript_available")
                    or resource_transcript_context
                )
                if not transcript_available_for_bootstrap:
                    control_text = (
                        "INTERNAL CONTROL: This session requires plan bootstrap and no structured resource text is available. "
                        "On your first spoken response after greeting, call mark_plan_fallback with a short reason, "
                        "then ask the student which milestone to start and call set_session_phase('tutoring'). "
                        "Do not mention this control message."
                    )
                else:
                    control_text = (
                        "INTERNAL CONTROL: This session requires a 0-to-hero milestone plan bootstrap. "
                        "On your first spoken response after greeting, you MUST call write_notes 6 to 10 times "
                        "with note_type='checklist_item' using unique titles 'Milestone 1 — ...', "
                        "'Milestone 2 — ...', etc., based on session_setup and any resource transcript context when available. "
                        "Then ask the student which milestone to start and call set_session_phase('tutoring'). "
                        "Do not mention this control message."
                    )
                send_content_with_prompt_capture(
                    live_queue,
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                text=control_text
                            )
                        ],
                    ),
                    session_id=session_id,
                    source="plan_bootstrap_control",
                    runtime_state=runtime_state,
                )
            logger.info(
                "Session %s run config: compression=%s(%s) resumption=%s(%s)",
                session_id,
                bool(run_config_meta.get("compression_enabled")),
                run_config_meta.get("compression_field"),
                bool(run_config_meta.get("resumption_enabled")),
                run_config_meta.get("resumption_field"),
            )

            forward_task = asyncio.create_task(
                _forward_to_gemini(
                    websocket, live_queue, session_id, runtime_state,
                    firestore_client=firestore_client,
                    latency_state=_latency_state,
                    debug_counters=_debug_counters,
                    debug_logger=_debug_logger,
                    report=report,
                ),
                name="forward_to_gemini",
            )
            receive_task = asyncio.create_task(
                _forward_to_client(
                    websocket,
                    runner,
                    live_queue,
                    session_id,
                    runtime_state,
                    wb_queue,
                    topic_queue,
                    firestore_client=firestore_client,
                    latency_state=_latency_state,
                    debug_counters=_debug_counters,
                    debug_logger=_debug_logger,
                    build_session_run_config=_build_session_run_config,
                    adk_stream_max_retries=ADK_STREAM_MAX_RETRIES,
                    adk_stream_retry_backoff_s=ADK_STREAM_RETRY_BACKOFF_S,
                    memory_checkpoint_interval_s=MEMORY_CHECKPOINT_INTERVAL_S,
                    live_compression_trigger_tokens=LIVE_COMPRESSION_TRIGGER_TOKENS,
                    live_compression_target_tokens=LIVE_COMPRESSION_TARGET_TOKENS,
                    response_ref_max_age_ms=RESPONSE_REF_MAX_AGE_MS,
                    turn_to_turn_max_gap_ms=TURN_TO_TURN_MAX_GAP_MS,
                    report=report,
                ),
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
                _session_heartbeat(session_id, runtime_state, _debug_counters, _debug_logger),
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
        try:
            await session_service.delete_session(
                app_name="seeme_tutor",
                user_id=raw_student_id,
                session_id=session_id,
            )
        except Exception:
            logger.warning(
                "Session %s: failed to cleanup in-memory ADK session",
                session_id,
                exc_info=True,
            )
        try:
            await _persist_memory_checkpoint(
                runtime_state,
                session_id,
                reason="session_end",
                firestore_client=firestore_client,
                checkpoint_interval_s=MEMORY_CHECKPOINT_INTERVAL_S,
                send_json=None,
                websocket=None,
                report=report,
                force=True,
            )
        except Exception:
            logger.warning("Session %s: final memory checkpoint failed", session_id, exc_info=True)
        try:
            latest_handle = str(runtime_state.get("session_resumption_handle") or "").strip()
            if latest_handle:
                await save_resumption_handle(
                    firestore_client,
                    student_id=raw_student_id,
                    session_id=session_id,
                    handle=latest_handle,
                )
        except Exception:
            logger.warning("Session %s: final resumption handle persist failed", session_id, exc_info=True)
        await _unregister_active_student_session(raw_student_id, session_id)
        unregister_whiteboard_queue(session_id)
        unregister_topic_update_queue(session_id)
        _latency_state.pop(session_id, None)
        _debug_counters.pop(session_id, None)
        duration = int(time.time() - session_start)
        if firestore_client:
            try:
                now_ts = time.time()
                update_payload: dict = {
                    "updated_at": now_ts,
                    "duration_seconds": duration,
                }
                # Ending the live call never closes the session — it stays
                # "open" so the student can resume later.  Only mastery
                # (or a future explicit abandon action) should change status.
                update_payload.update(
                    {
                        "status": "open",
                        "ended_reason": ended_reason,
                        "last_disconnect_at": now_ts,
                    }
                )
                await firestore_client.collection("sessions").document(session_id).set(
                    update_payload,
                    merge=True,
                )
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
            if firestore_client:
                try:
                    await _persist_session_metrics_summary(session_id, report, firestore_client)
                except Exception:
                    logger.warning("Session %s: failed to persist telemetry summary", session_id, exc_info=True)
            try:
                report.save()
            except Exception:
                logger.warning("Session %s: failed to save test report", session_id, exc_info=True)
            remove_report(session_id)


# _session_heartbeat, _session_timer, _mark_student_activity, _mark_proactive_response_seen,
# _is_probable_speech_pcm16, _resume_from_away, _apply_checkpoint_decision, _merge_resume_message
# moved to modules.session_helpers


# _load_memory_recall, _persist_memory_checkpoint, _persist_session_metrics_summary,
# _log_command_event moved to modules.persistence

# _forward_to_gemini, _iter_runner_events_with_retry, _forward_to_client,
# _send_json, _StudentEndedSession moved to modules.ws_bridge
