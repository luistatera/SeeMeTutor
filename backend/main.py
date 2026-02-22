"""
SeeMe Tutor — FastAPI backend.

Bridges browser WebSocket connections to the Gemini Live API.
Audio and video frames flow from the browser to Gemini; audio responses
and text transcripts flow back to the browser.
"""

import asyncio
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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from google.adk import Runner
from google.adk.sessions import InMemorySessionService

from gemini_live import (
    ADKLiveSession,
    APP_NAME,
    register_whiteboard_queue,
    unregister_whiteboard_queue,
    register_topic_update_queue,
    unregister_topic_update_queue,
)
from tutor_agent.agent import root_agent

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
# Gemini backend: Vertex AI (default) or Developer API key (local fallback)
# ---------------------------------------------------------------------------
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
DEMO_ACCESS_CODE = os.environ.get("DEMO_ACCESS_CODE", "")
if _GEMINI_API_KEY:
    # Local dev without gcloud auth — use Developer API key
    os.environ["GOOGLE_API_KEY"] = _GEMINI_API_KEY
    os.environ.pop("GEMINI_API_KEY", None)
    logger.info("Gemini backend: Developer API (API key set)")
else:
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
PACE_CONTROL_INSTRUCTIONS: dict[str, str] = {
    "slow": (
        "Preference update: from now on, speak noticeably slower. "
        "Use shorter sentences, clearer articulation, and brief pauses between ideas. "
        "Keep the same warm tutoring style unless the student asks to change pace again."
    ),
}

# Per-session latency tracking: session_id -> {"last_audio_in": float, "awaiting_first_response": bool}
_latency_state: dict[str, dict] = {}
# Per-session debug counters: session_id -> {audio_in: int, audio_out: int, ...}
_debug_counters: dict[str, dict] = {}
_active_student_sessions: dict[str, dict] = {}
_active_student_sessions_lock = asyncio.Lock()

_STUDENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SUPPORTED_LANGUAGE_MODES = {"guided_bilingual", "immersion", "auto"}


def _anonymize_ip(ip: str) -> str:
    """Hash an IP address for Firestore storage (never persist raw IPs)."""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _language_label(code: str) -> str:
    normalized = str(code or "").strip().lower()
    if normalized.startswith("en"):
        return "English"
    if normalized.startswith("pt"):
        return "Portuguese"
    if normalized.startswith("de"):
        return "German"
    return code or "English"


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
    normalized = str(value or "").strip().lower()
    if normalized.startswith("de"):
        return "de"
    if normalized.startswith("pt"):
        return "pt"
    if normalized.startswith("en"):
        return "en"
    return normalized or "en"


def _default_language_policy() -> dict:
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


def _normalize_language_policy(policy: dict | None, fallback: dict) -> dict:
    source = policy if isinstance(policy, dict) else {}
    fallback_confusion = fallback.get("confusion_fallback", {})
    source_confusion = source.get("confusion_fallback", {}) if isinstance(source.get("confusion_fallback"), dict) else {}

    mode = str(source.get("mode") or fallback.get("mode") or "auto").strip().lower()
    if mode not in _SUPPORTED_LANGUAGE_MODES:
        mode = str(fallback.get("mode") or "auto")

    normalized = {
        "policy_version": str(source.get("policy_version") or fallback.get("policy_version") or "v1"),
        "mode": mode,
        "l1": str(source.get("l1") or fallback.get("l1") or "en-US"),
        "l2": str(source.get("l2") or fallback.get("l2") or "en-US"),
        "explain_language": str(source.get("explain_language") or fallback.get("explain_language") or "l1"),
        "practice_language": str(source.get("practice_language") or fallback.get("practice_language") or "l2"),
        "no_mixed_language_same_turn": bool(
            source.get("no_mixed_language_same_turn")
            if source.get("no_mixed_language_same_turn") is not None
            else fallback.get("no_mixed_language_same_turn", True)
        ),
        "max_l2_turns_before_recap": _parse_int(
            source.get("max_l2_turns_before_recap"),
            _parse_int(fallback.get("max_l2_turns_before_recap"), 3),
            minimum=1,
            maximum=6,
        ),
        "confusion_fallback": {
            "after_confusions": _parse_int(
                source_confusion.get("after_confusions"),
                _parse_int(fallback_confusion.get("after_confusions"), 2),
                minimum=1,
                maximum=5,
            ),
            "fallback_language": str(
                source_confusion.get("fallback_language")
                or fallback_confusion.get("fallback_language")
                or "l1"
            ),
            "fallback_turns": _parse_int(
                source_confusion.get("fallback_turns"),
                _parse_int(fallback_confusion.get("fallback_turns"), 2),
                minimum=1,
                maximum=6,
            ),
        },
    }
    return normalized


def _build_language_contract(language_policy: dict) -> str:
    mode = language_policy.get("mode", "auto")
    l1 = language_policy.get("l1", "en-US")
    l2 = language_policy.get("l2", "en-US")
    l1_label = _language_label(l1)
    l2_label = _language_label(l2)
    no_mix = bool(language_policy.get("no_mixed_language_same_turn", True))
    max_l2_turns = _parse_int(language_policy.get("max_l2_turns_before_recap"), 3, minimum=1, maximum=6)
    confusion = language_policy.get("confusion_fallback", {}) if isinstance(language_policy.get("confusion_fallback"), dict) else {}
    after_confusions = _parse_int(confusion.get("after_confusions"), 2, minimum=1, maximum=5)
    fallback_turns = _parse_int(confusion.get("fallback_turns"), 2, minimum=1, maximum=6)
    fallback_language_key = str(confusion.get("fallback_language") or "l1").lower()
    fallback_language = l1_label if fallback_language_key == "l1" else l2_label

    contract_parts = [
        f"Mode: {mode}.",
        f"L1: {l1_label}.",
        f"L2: {l2_label}.",
    ]
    if mode == "guided_bilingual":
        contract_parts.extend([
            f"Use {l1_label} for explanations and strategy coaching.",
            f"Use {l2_label} for practice drills and output exercises.",
            "When switching languages, say a short transition sentence first.",
            f"After at most {max_l2_turns} consecutive L2 practice turns, return to a short L1 recap.",
        ])
    elif mode == "immersion":
        contract_parts.extend([
            f"Default to {l2_label} for almost all tutor turns.",
            f"Use {l1_label} only if the learner requests it or shows repeated confusion.",
        ])
    else:
        contract_parts.extend([
            "Follow the learner's active language preference naturally.",
            "If language preference is ambiguous, default to L1.",
        ])

    if no_mix:
        contract_parts.append("Never mix two languages in the same tutor response.")
    contract_parts.append(
        f"If confusion appears {after_confusions} times in a row, force {fallback_language} for the next {fallback_turns} turns before retrying."
    )
    return " ".join(contract_parts)


def _default_backlog_context(student_id: str, student_name: str = "Student") -> dict:
    language_policy = _default_language_policy()
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
    preferred_language = _normalize_preferred_language(
        student_data.get("preferred_language") or language_policy.get("l2") or "en"
    )
    student_updates: dict = {}
    if student_data.get("language_policy") != language_policy:
        student_updates["language_policy"] = language_policy
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
    }


async def _build_profile_summary(student_snapshot) -> dict:
    """Build lightweight profile data for profile picker UI."""
    student_data = student_snapshot.to_dict() or {}
    student_id = student_snapshot.id
    student_ref = student_snapshot.reference
    student_name = str(student_data.get("name") or student_id)
    language_policy = _normalize_language_policy(student_data.get("language_policy"), _default_language_policy())
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
FRONTEND_DIR = BASE_DIR.parent / "frontend"
if not FRONTEND_DIR.is_dir():
    FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(
    title="SeeMe Tutor",
    description="Real-time multimodal AI tutoring via Gemini Live API.",
    version="1.0.0",
)

_session_service = InMemorySessionService()
_runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=_session_service,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

    raw_ip = websocket.headers.get("x-forwarded-for", websocket.client.host if websocket.client else "unknown")
    client_host = raw_ip.split(",")[0].strip()
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

    # Create ADK session with state accessible to tools (e.g. log_progress)
    await _session_service.create_session(
        app_name=APP_NAME,
        user_id=raw_student_id,
        session_id=session_id,
        state={
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
            "previous_notes": backlog_context.get("previous_notes", []),
            "session_phase": "greeting",
        },
    )
    await _send_json(websocket, {"type": "backlog_context", "data": backlog_context})
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
        "resume_message": backlog_context.get("resume_message"),
        "student_id": raw_student_id,
        "student_name": backlog_context.get("student_name"),
        "track_id": backlog_context.get("track_id"),
        "topic_id": backlog_context.get("topic_id"),
        "topic_title": backlog_context.get("topic_title"),
    }
    wb_queue = register_whiteboard_queue(session_id)
    topic_queue = register_topic_update_queue(session_id)
    ended_reason = "disconnect"
    try:
        try:
            async with ADKLiveSession(
                runner=_runner,
                user_id=raw_student_id,
                session_id=session_id,
            ) as gemini_session:
                forward_task = asyncio.create_task(
                    _forward_to_gemini(websocket, gemini_session, session_id, runtime_state),
                    name="forward_to_gemini",
                )
                receive_task = asyncio.create_task(
                    _forward_to_client(websocket, gemini_session, session_id, runtime_state, wb_queue, topic_queue),
                    name="forward_to_client",
                )
                timer_task = asyncio.create_task(
                    _session_timer(websocket, SESSION_TIMEOUT_SECONDS),
                    name="session_timer",
                )
                idle_task = asyncio.create_task(
                    _idle_orchestrator(websocket, runtime_state),
                    name="idle_orchestrator",
                )
                heartbeat_task = asyncio.create_task(
                    _session_heartbeat(session_id, runtime_state),
                    name="session_heartbeat",
                )

                done, pending = await asyncio.wait(
                    {forward_task, receive_task, timer_task, idle_task, heartbeat_task},
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
            logger.exception("Session %s: Gemini session error: %s", session_id, exc)
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


async def _idle_orchestrator(websocket: WebSocket, runtime_state: dict) -> None:
    """Drive deterministic idle check-ins and away-mode transitions."""
    while True:
        await asyncio.sleep(0.5)
        if runtime_state.get("away_mode"):
            continue
        if not runtime_state.get("mic_active"):
            continue
        if runtime_state.get("assistant_speaking"):
            continue

        now = time.time()
        last_activity = runtime_state.get("last_user_activity_at", now)
        idle_for = now - last_activity
        idle_stage = runtime_state.get("idle_stage", 0)
        mic_opened_at = runtime_state.get("mic_opened_at")

        # Start the conversation only after the mic has been opened and
        # the learner has been quiet for a few seconds. If Gemini already
        # started a real turn, suppress this kickoff prompt.
        if (
            not runtime_state.get("conversation_started")
            and not runtime_state.get("mic_kickoff_sent")
            and mic_opened_at is not None
        ):
            mic_open_for = now - float(mic_opened_at)
            if mic_open_for >= MIC_KICKOFF_SECONDS and idle_for >= MIC_KICKOFF_SECONDS:
                runtime_state["mic_kickoff_sent"] = True
                runtime_state["conversation_started"] = True
                runtime_state["last_user_activity_at"] = now
                runtime_state["idle_stage"] = 0
                topic_title = runtime_state.get("topic_title") or "your current topic"
                await _send_json(websocket, {"type": "assistant_state", "data": {"state": "active", "reason": "mic_kickoff"}})
                await _send_json(websocket, {
                    "type": "assistant_prompt",
                    "data": f"Let's begin with {topic_title}. Tell me where you want to start.",
                })
                continue

        # Do not send idle check-ins before a real turn has started.
        if not runtime_state.get("conversation_started"):
            continue

        if idle_stage < 1 and idle_for >= IDLE_CHECKIN_1_SECONDS:
            runtime_state["idle_stage"] = 1
            runtime_state["last_user_activity_at"] = now
            await _send_json(websocket, {"type": "assistant_state", "data": {"state": "idle_checkin_1"}})
            await _send_json(websocket, {
                "type": "assistant_prompt",
                "data": "Still with me? Take your time — I can wait while you think.",
            })
            continue

        if idle_stage < 2 and idle_for >= IDLE_CHECKIN_2_SECONDS:
            runtime_state["idle_stage"] = 2
            runtime_state["last_user_activity_at"] = now
            await _send_json(websocket, {"type": "assistant_state", "data": {"state": "idle_checkin_2"}})
            await _send_json(websocket, {
                "type": "assistant_prompt",
                "data": "Would you like a short pause? Say 'I'm back' whenever you want to continue.",
            })
            continue

        if idle_for >= IDLE_AUTO_AWAY_SECONDS:
            runtime_state["away_mode"] = True
            runtime_state["last_user_activity_at"] = now
            await _send_json(websocket, {"type": "assistant_state", "data": {"state": "away", "reason": "idle_timeout"}})
            await _send_json(websocket, {
                "type": "assistant_prompt",
                "data": "No rush. I'll wait here quietly until you come back.",
            })


async def _resume_from_away(websocket: WebSocket, runtime_state: dict) -> None:
    runtime_state["away_mode"] = False
    runtime_state["idle_stage"] = 0
    runtime_state["last_user_activity_at"] = time.time()
    runtime_state["conversation_started"] = True
    runtime_state["mic_kickoff_sent"] = True
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
    }
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
    session: ADKLiveSession,
    session_id: str,
    runtime_state: dict,
) -> None:
    """
    Receive JSON messages from the browser and forward media to Gemini.

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
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                runtime_state["conversation_started"] = True
                runtime_state["mic_kickoff_sent"] = True
                if runtime_state.get("away_mode"):
                    await _resume_from_away(websocket, runtime_state)
                continue
            if msg_type == "mic_start":
                now = time.time()
                runtime_state["mic_active"] = True
                runtime_state["mic_opened_at"] = now
                runtime_state["mic_kickoff_sent"] = False
                runtime_state["conversation_started"] = False
                runtime_state["last_user_activity_at"] = now
                runtime_state["idle_stage"] = 0
                logger.info("Control message from browser: 'mic_start'")
                continue
            if msg_type == "away_mode":
                active = bool(message.get("active", True))
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                runtime_state["away_mode"] = active
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
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                runtime_state["conversation_started"] = True
                runtime_state["mic_kickoff_sent"] = True
                try:
                    session.send_text(instruction, role="user")
                except Exception:
                    logger.warning("Session %s: failed to forward speech pace command", session_id, exc_info=True)
                continue
            if msg_type == "barge_in":
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                runtime_state["assistant_speaking"] = False
                runtime_state["conversation_started"] = True
                runtime_state["mic_kickoff_sent"] = True
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
                logger.info("Control message from browser: '%s'", msg_type)
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
                runtime_state["last_user_activity_at"] = now
                runtime_state["idle_stage"] = 0
                runtime_state["conversation_started"] = True
                runtime_state["mic_kickoff_sent"] = True
                lat = _latency_state.get(session_id)
                if lat is not None:
                    lat["last_audio_in"] = now
                    lat["awaiting_first_response"] = True
                dc = _debug_counters.get(session_id)
                if dc is not None:
                    dc["audio_in"] += 1
                    dc["last_audio_in_at"] = now
                session.send_audio(raw_bytes)
                audio_chunks_sent += 1
            elif msg_type == "video":
                dc = _debug_counters.get(session_id)
                if dc is not None:
                    dc["video_in"] += 1
                session.send_video_frame(raw_bytes)
                video_frames_sent += 1
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
    except _StudentEndedSession:
        raise
    except Exception as exc:
        logger.exception("Unexpected error in forward_to_gemini: %s", exc)
        await _send_json(websocket, {
            "type": "error",
            "data": "The connection to the tutor was interrupted. Please refresh to start a new session.",
        })


async def _forward_to_client(
    websocket: WebSocket,
    session: ADKLiveSession,
    session_id: str = "",
    runtime_state: dict | None = None,
    wb_queue: asyncio.Queue | None = None,
    topic_queue: asyncio.Queue | None = None,
) -> None:
    """
    Receive responses from Gemini and forward them to the browser.

    Runs until the Gemini session closes, the WebSocket disconnects,
    or an unrecoverable error occurs.
    """
    audio_response_chunks = 0
    turn_count = 0

    try:
        runtime_state = runtime_state or {}
        async for event in session.receive():
            # Drain any pending whiteboard notes queued by write_notes tool
            if wb_queue is not None:
                while not wb_queue.empty():
                    try:
                        note = wb_queue.get_nowait()
                        await _send_json(websocket, {"type": "whiteboard", "data": note})
                    except asyncio.QueueEmpty:
                        break

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

            event_type = event.get("type")
            dc = _debug_counters.get(session_id)
            if dc is not None:
                dc["last_gemini_event_at"] = time.time()

            if event_type == "audio":
                was_speaking = runtime_state.get("assistant_speaking")
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                runtime_state["assistant_speaking"] = True
                runtime_state["conversation_started"] = True
                runtime_state["mic_kickoff_sent"] = True
                if not was_speaking:
                    _debug_logger.debug(
                        "SPEAKING_START sid=%s (was silent, now audio)",
                        session_id[:8],
                    )
                lat = _latency_state.get(session_id)
                if lat and lat["awaiting_first_response"] and lat["last_audio_in"] > 0:
                    delta_ms = (time.time() - lat["last_audio_in"]) * 1000
                    logger.info(
                        "LATENCY session=%s response_start_ms=%.0f",
                        session_id, delta_ms,
                    )
                    lat["awaiting_first_response"] = False
                if dc is not None:
                    dc["audio_out"] += 1
                audio_bytes: bytes = event["data"]
                encoded = base64.b64encode(audio_bytes).decode("utf-8")
                await _send_json(websocket, {"type": "audio", "data": encoded})
                audio_response_chunks += 1

            elif event_type == "text":
                logger.info("TUTOR TRANSCRIPT: %s", event["data"])
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                runtime_state["assistant_speaking"] = True
                runtime_state["conversation_started"] = True
                runtime_state["mic_kickoff_sent"] = True
                if dc is not None:
                    dc["text_out"] += 1
                _debug_logger.debug(
                    "TEXT sid=%s data=%s",
                    session_id[:8], str(event["data"])[:120],
                )
                await _send_json(websocket, {"type": "text", "data": event["data"]})

            elif event_type == "input_transcript":
                logger.info("STUDENT HEARD: %s", event["data"])

            elif event_type == "turn_complete":
                turn_count += 1
                runtime_state["assistant_speaking"] = False
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                if dc is not None:
                    dc["turn_complete"] += 1
                _debug_logger.debug("TURN_COMPLETE sid=%s", session_id[:8])
                await _send_json(websocket, {"type": "turn_complete"})
                logger.info(
                    "Turn #%d complete — sent %d audio chunks to browser",
                    turn_count, audio_response_chunks,
                )
                audio_response_chunks = 0

            elif event_type == "interrupted":
                runtime_state["assistant_speaking"] = False
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                if dc is not None:
                    dc["interrupted"] += 1
                _debug_logger.debug("INTERRUPTED sid=%s", session_id[:8])
                lat = _latency_state.get(session_id)
                if lat and lat["last_audio_in"] > 0:
                    delta_ms = (time.time() - lat["last_audio_in"]) * 1000
                    logger.info(
                        "LATENCY session=%s interruption_stop_ms=%.0f",
                        session_id, delta_ms,
                    )
                    lat["awaiting_first_response"] = False
                await _send_json(websocket, {"type": "interrupted"})
                logger.info(
                    "INTERRUPTED by student (had sent %d audio chunks before interruption)",
                    audio_response_chunks,
                )
                audio_response_chunks = 0

            else:
                _debug_logger.debug(
                    "UNKNOWN_EVENT sid=%s type=%s", session_id[:8], event_type,
                )
                logger.warning("Unknown event type from Gemini session: '%s'", event_type)

    except WebSocketDisconnect:
        logger.info("Browser disconnected (forward_to_client)")
    except Exception as exc:
        logger.exception("Unexpected error in forward_to_client: %s", exc)
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
