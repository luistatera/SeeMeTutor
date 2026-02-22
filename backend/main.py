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

from gemini_live import ADKLiveSession, APP_NAME, register_whiteboard_queue, unregister_whiteboard_queue
from tutor_agent.agent import root_agent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
if GEMINI_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY
    os.environ.pop("GEMINI_API_KEY", None)
else:
    logger.warning(
        "GEMINI_API_KEY is not set. WebSocket connections will fail. "
        "Set the variable in .env or as an environment variable."
    )

DEMO_ACCESS_CODE = os.environ.get("DEMO_ACCESS_CODE", "")

SESSION_TIMEOUT_SECONDS = 20 * 60  # 20-minute focused session limit
IDLE_CHECKIN_1_SECONDS = 10
IDLE_CHECKIN_2_SECONDS = 25
IDLE_AUTO_AWAY_SECONDS = 90
MIC_KICKOFF_SECONDS = 5

# Per-session latency tracking: session_id -> {"last_audio_in": float, "awaiting_first_response": bool}
_latency_state: dict[str, dict] = {}

DEFAULT_PROFILE_SEEDS: dict[str, dict] = {
    "luis-german": {
        "name": "Luis",
        "preferred_language": "de",
        "language_policy": {
            "policy_version": "v1",
            "mode": "guided_bilingual",
            "l1": "en-US",
            "l2": "de-DE",
            "explain_language": "l1",
            "practice_language": "l2",
            "no_mixed_language_same_turn": True,
            "max_l2_turns_before_recap": 2,
            "confusion_fallback": {
                "after_confusions": 2,
                "fallback_language": "l1",
                "fallback_turns": 3,
            },
        },
        "track": {
            "id": "german-a2",
            "title": "German A2",
            "domain": "language",
            "goal": "Build confidence in daily family conversations.",
        },
        "topics": [
            {"id": "numbers-dates", "title": "Numbers and Dates", "order_index": 1, "status": "in_progress"},
            {"id": "daily-conversation", "title": "Daily Conversations", "order_index": 2, "status": "not_started"},
            {"id": "separable-verbs", "title": "Separable Verbs", "order_index": 3, "status": "not_started"},
        ],
    },
    "daughter-math7": {
        "name": "Daughter — Math",
        "preferred_language": "en",
        "language_policy": {
            "policy_version": "v1",
            "mode": "immersion",
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
        },
        "track": {
            "id": "math-grade-7",
            "title": "Math Grade 7",
            "domain": "math",
            "goal": "Master grade 7 equations and proportional reasoning.",
        },
        "topics": [
            {"id": "fractions", "title": "Fractions", "order_index": 1, "status": "in_progress"},
            {"id": "linear-equations", "title": "Linear Equations", "order_index": 2, "status": "not_started"},
            {"id": "ratios-proportions", "title": "Ratios and Proportions", "order_index": 3, "status": "not_started"},
        ],
    },
    "daughter-chem-university": {
        "name": "Daughter — Chemistry",
        "preferred_language": "pt",
        "language_policy": {
            "policy_version": "v1",
            "mode": "immersion",
            "l1": "pt-BR",
            "l2": "pt-BR",
            "explain_language": "l1",
            "practice_language": "l2",
            "no_mixed_language_same_turn": True,
            "max_l2_turns_before_recap": 3,
            "confusion_fallback": {
                "after_confusions": 2,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        },
        "track": {
            "id": "chem-university",
            "title": "University Chemistry",
            "domain": "chemistry",
            "goal": "Understand reaction balancing and molecular reasoning.",
        },
        "topics": [
            {"id": "stoichiometry", "title": "Stoichiometry", "order_index": 1, "status": "in_progress"},
            {"id": "equilibrium", "title": "Chemical Equilibrium", "order_index": 2, "status": "not_started"},
            {"id": "organic-functional-groups", "title": "Organic Functional Groups", "order_index": 3, "status": "not_started"},
        ],
    },
    "wife-technology": {
        "name": "Wife",
        "preferred_language": "pt",
        "language_policy": {
            "policy_version": "v1",
            "mode": "auto",
            "l1": "pt-BR",
            "l2": "pt-BR",
            "explain_language": "l1",
            "practice_language": "l2",
            "no_mixed_language_same_turn": True,
            "max_l2_turns_before_recap": 3,
            "confusion_fallback": {
                "after_confusions": 2,
                "fallback_language": "l1",
                "fallback_turns": 2,
            },
        },
        "track": {
            "id": "technology-skills",
            "title": "Technology Skills",
            "domain": "technology",
            "goal": "Gain practical confidence with daily computer tasks.",
        },
        "topics": [
            {"id": "email-workflow", "title": "Email Workflow", "order_index": 1, "status": "in_progress"},
            {"id": "files-folders", "title": "Files and Folders", "order_index": 2, "status": "not_started"},
            {"id": "video-calls", "title": "Video Calls", "order_index": 3, "status": "not_started"},
        ],
    },
}

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


def _default_language_policy(seed: dict | None = None) -> dict:
    if isinstance(seed, dict) and isinstance(seed.get("language_policy"), dict):
        policy = seed["language_policy"]
        if isinstance(policy, dict):
            return policy
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


def _default_backlog_context(student_id: str) -> dict:
    seed = DEFAULT_PROFILE_SEEDS.get(student_id)
    if not seed:
        language_policy = _default_language_policy()
        return {
            "student_id": student_id,
            "student_name": "Student",
            "preferred_language": "en",
            "track_id": "general-track",
            "track_title": "General Learning",
            "topic_id": "current-topic",
            "topic_title": "Current Topic",
            "topic_status": "in_progress",
            "unresolved_topics": 0,
            "resume_message": "Let's continue where we left off.",
            "language_policy": language_policy,
            "language_contract": _build_language_contract(language_policy),
        }

    track = seed["track"]
    active_topic = seed["topics"][0]
    language_policy = _normalize_language_policy(seed.get("language_policy"), _default_language_policy(seed))
    return {
        "student_id": student_id,
        "student_name": seed["name"],
        "preferred_language": seed.get("preferred_language", "en"),
        "track_id": track["id"],
        "track_title": track["title"],
        "topic_id": active_topic["id"],
        "topic_title": active_topic["title"],
        "topic_status": active_topic["status"],
        "unresolved_topics": 0,
        "resume_message": f"Welcome back, {seed['name']}. Last time we were working on {active_topic['title']}.",
        "language_policy": language_policy,
        "language_contract": _build_language_contract(language_policy),
    }


async def _load_or_seed_backlog_context(student_id: str) -> dict:
    """Ensure student profile + backlog exists and return active context."""
    seed = DEFAULT_PROFILE_SEEDS.get(student_id)
    default_context = _default_backlog_context(student_id)
    seed_language_policy = _normalize_language_policy(
        seed.get("language_policy") if isinstance(seed, dict) else None,
        _default_language_policy(seed),
    )
    if not seed or not firestore_client:
        return default_context

    now = time.time()
    student_ref = firestore_client.collection("students").document(student_id)
    student_snapshot = await student_ref.get()

    if not student_snapshot.exists:
        track_seed = seed["track"]
        first_topic = seed["topics"][0]
        await student_ref.set({
            "name": seed["name"],
            "preferred_language": seed["preferred_language"],
            "language_policy": seed_language_policy,
            "active_track_id": track_seed["id"],
            "last_active_topic_id": first_topic["id"],
            "last_session_summary": "",
            "created_at": now,
            "updated_at": now,
        })
    else:
        student_data = student_snapshot.to_dict() or {}
        updates = {}
        if not student_data.get("active_track_id"):
            updates["active_track_id"] = seed["track"]["id"]
        if not student_data.get("last_active_topic_id"):
            updates["last_active_topic_id"] = seed["topics"][0]["id"]
        if not isinstance(student_data.get("language_policy"), dict):
            updates["language_policy"] = seed_language_policy
        if not student_data.get("preferred_language"):
            updates["preferred_language"] = seed.get("preferred_language", "en")
        if updates:
            updates["updated_at"] = now
            await student_ref.update(updates)

    track_seed = seed["track"]
    track_ref = student_ref.collection("tracks").document(track_seed["id"])
    track_snapshot = await track_ref.get()
    if not track_snapshot.exists:
        await track_ref.set({
            "title": track_seed["title"],
            "domain": track_seed["domain"],
            "goal": track_seed["goal"],
            "status": "active",
            "created_at": now,
            "updated_at": now,
        })

    for topic_seed in seed["topics"]:
        topic_ref = track_ref.collection("topics").document(topic_seed["id"])
        topic_snapshot = await topic_ref.get()
        if topic_snapshot.exists:
            continue
        await topic_ref.set({
            "title": topic_seed["title"],
            "order_index": topic_seed["order_index"],
            "status": topic_seed["status"],
            "struggle_count": 0,
            "success_count": 0,
            "checkpoint_open": False,
            "next_step_hint": "",
            "last_seen_session_id": None,
            "last_seen_at": None,
            "created_at": now,
            "updated_at": now,
        })

    student_snapshot = await student_ref.get()
    student_data = student_snapshot.to_dict() or {}
    active_track_id = student_data.get("active_track_id") or track_seed["id"]
    active_topic_id = student_data.get("last_active_topic_id") or seed["topics"][0]["id"]

    active_track_ref = student_ref.collection("tracks").document(active_track_id)
    active_track_snapshot = await active_track_ref.get()
    active_track_data = active_track_snapshot.to_dict() or {"title": track_seed["title"]}

    active_topic_ref = active_track_ref.collection("topics").document(active_topic_id)
    active_topic_snapshot = await active_topic_ref.get()
    if not active_topic_snapshot.exists:
        first_topic_id = seed["topics"][0]["id"]
        active_topic_ref = active_track_ref.collection("topics").document(first_topic_id)
        active_topic_snapshot = await active_topic_ref.get()
        await student_ref.update({"last_active_topic_id": first_topic_id, "updated_at": now})

    active_topic_data = active_topic_snapshot.to_dict() or {
        "title": default_context["topic_title"],
        "status": "in_progress",
    }

    topic_rows: list[dict] = []
    unresolved_topics = 0
    async for topic_snapshot in active_track_ref.collection("topics").stream():
        topic_data = topic_snapshot.to_dict() or {}
        if topic_data.get("checkpoint_open"):
            unresolved_topics += 1
        topic_rows.append({
            "id": topic_snapshot.id,
            "title": topic_data.get("title") or topic_snapshot.id,
            "status": str(topic_data.get("status", "not_started")).lower(),
            "order_index": int(topic_data.get("order_index", 9999)),
        })

    topic_rows.sort(key=lambda t: t["order_index"])
    previous_topic_title = active_topic_data.get("title") or default_context["topic_title"]
    active_topic_status = str(active_topic_data.get("status", "in_progress")).lower()
    if active_topic_status == "mastered":
        next_topic = None
        active_index = next((idx for idx, row in enumerate(topic_rows) if row["id"] == active_topic_ref.id), -1)

        if active_index >= 0:
            for row in topic_rows[active_index + 1:]:
                if row["status"] != "mastered":
                    next_topic = row
                    break

        if not next_topic:
            for row in topic_rows:
                if row["status"] != "mastered":
                    next_topic = row
                    break

        if next_topic:
            active_topic_ref = active_track_ref.collection("topics").document(next_topic["id"])
            active_topic_snapshot = await active_topic_ref.get()
            active_topic_data = active_topic_snapshot.to_dict() or {
                "title": next_topic["title"],
                "status": next_topic["status"],
            }
            await student_ref.update({
                "last_active_topic_id": next_topic["id"],
                "updated_at": now,
            })

    student_name = student_data.get("name") or seed["name"]
    preferred_language = student_data.get("preferred_language") or seed.get("preferred_language", "en")
    language_policy = _normalize_language_policy(student_data.get("language_policy"), seed_language_policy)
    if student_data.get("language_policy") != language_policy:
        await student_ref.set({
            "language_policy": language_policy,
            "updated_at": now,
        }, merge=True)

    topic_title = active_topic_data.get("title") or default_context["topic_title"]
    topic_status = str(active_topic_data.get("status", "in_progress")).lower()
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
    return {
        "student_id": student_id,
        "student_name": student_name,
        "preferred_language": preferred_language,
        "track_id": active_track_id,
        "track_title": active_track_data.get("title", track_seed["title"]),
        "topic_id": active_topic_ref.id,
        "topic_title": topic_title,
        "topic_status": topic_status,
        "unresolved_topics": unresolved_topics,
        "resume_message": resume_message,
        "language_policy": language_policy,
        "language_contract": _build_language_contract(language_policy),
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

    backlog_context = await _load_or_seed_backlog_context(raw_student_id)

    raw_ip = websocket.headers.get("x-forwarded-for", websocket.client.host if websocket.client else "unknown")
    client_host = raw_ip.split(",")[0].strip()
    session_id = str(uuid.uuid4())
    session_start = time.time()
    logger.info("Session %s accepted from %s", session_id, client_host)

    if not GEMINI_API_KEY:
        await _send_json(websocket, {"type": "error", "data": "Server misconfiguration: API key not set."})
        await websocket.close()
        return

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
            "language_policy": backlog_context.get("language_policy"),
            "language_contract": backlog_context.get("language_contract"),
        },
    )
    await _send_json(websocket, {"type": "backlog_context", "data": backlog_context})

    _latency_state[session_id] = {"last_audio_in": 0.0, "awaiting_first_response": False}
    runtime_state = {
        "last_user_activity_at": time.time(),
        "idle_stage": 0,  # 0=none, 1=first check-in sent, 2=second check-in sent
        "away_mode": False,
        "assistant_speaking": False,
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
                    _forward_to_client(websocket, gemini_session, session_id, runtime_state, wb_queue),
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

                done, pending = await asyncio.wait(
                    {forward_task, receive_task, timer_task, idle_task},
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
        unregister_whiteboard_queue(session_id)
        _latency_state.pop(session_id, None)
        duration = int(time.time() - session_start)
        if firestore_client:
            try:
                await firestore_client.collection("sessions").document(session_id).update({
                    "ended_reason": ended_reason,
                    "duration_seconds": duration,
                })
            except Exception:
                logger.warning("Session %s: failed to log end to Firestore", session_id, exc_info=True)
        logger.info("Session %s ended after %ds (reason: %s)", session_id, duration, ended_reason)


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
        # the learner has been quiet for a few seconds.
        if not runtime_state.get("mic_kickoff_sent") and mic_opened_at is not None:
            mic_open_for = now - float(mic_opened_at)
            if mic_open_for >= MIC_KICKOFF_SECONDS and idle_for >= MIC_KICKOFF_SECONDS:
                runtime_state["mic_kickoff_sent"] = True
                runtime_state["last_user_activity_at"] = now
                runtime_state["idle_stage"] = 0
                topic_title = runtime_state.get("topic_title") or "your current topic"
                await _send_json(websocket, {"type": "assistant_state", "data": {"state": "active", "reason": "mic_kickoff"}})
                await _send_json(websocket, {
                    "type": "assistant_prompt",
                    "data": f"Let's begin with {topic_title}. Tell me where you want to start.",
                })
                continue

        if idle_stage < 1 and idle_for >= IDLE_CHECKIN_1_SECONDS:
            runtime_state["idle_stage"] = 1
            await _send_json(websocket, {"type": "assistant_state", "data": {"state": "idle_checkin_1"}})
            await _send_json(websocket, {
                "type": "assistant_prompt",
                "data": "Still with me? Take your time — I can wait while you think.",
            })
            continue

        if idle_stage < 2 and idle_for >= IDLE_CHECKIN_2_SECONDS:
            runtime_state["idle_stage"] = 2
            await _send_json(websocket, {"type": "assistant_state", "data": {"state": "idle_checkin_2"}})
            await _send_json(websocket, {
                "type": "assistant_prompt",
                "data": "Would you like a short pause? Say 'I'm back' whenever you want to continue.",
            })
            continue

        if idle_for >= IDLE_AUTO_AWAY_SECONDS:
            runtime_state["away_mode"] = True
            await _send_json(websocket, {"type": "assistant_state", "data": {"state": "away", "reason": "idle_timeout"}})
            await _send_json(websocket, {
                "type": "assistant_prompt",
                "data": "No rush. I'll wait here quietly until you come back.",
            })


async def _resume_from_away(websocket: WebSocket, runtime_state: dict) -> None:
    runtime_state["away_mode"] = False
    runtime_state["idle_stage"] = 0
    runtime_state["last_user_activity_at"] = time.time()
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
                if runtime_state.get("away_mode"):
                    await _resume_from_away(websocket, runtime_state)
                continue
            if msg_type == "mic_start":
                now = time.time()
                runtime_state["mic_active"] = True
                runtime_state["mic_opened_at"] = now
                runtime_state["mic_kickoff_sent"] = False
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
                lat = _latency_state.get(session_id)
                if lat is not None:
                    lat["last_audio_in"] = time.time()
                    lat["awaiting_first_response"] = True
                session.send_audio(raw_bytes)
            elif msg_type == "video":
                session.send_video_frame(raw_bytes)
            else:
                logger.warning("Unknown message type from browser: '%s'", msg_type)

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
) -> None:
    """
    Receive responses from Gemini and forward them to the browser.

    Runs until the Gemini session closes, the WebSocket disconnects,
    or an unrecoverable error occurs.
    """
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

            event_type = event.get("type")

            if event_type == "audio":
                runtime_state["assistant_speaking"] = True
                lat = _latency_state.get(session_id)
                if lat and lat["awaiting_first_response"] and lat["last_audio_in"] > 0:
                    delta_ms = (time.time() - lat["last_audio_in"]) * 1000
                    logger.info(
                        "LATENCY session=%s response_start_ms=%.0f",
                        session_id, delta_ms,
                    )
                    lat["awaiting_first_response"] = False
                audio_bytes: bytes = event["data"]
                encoded = base64.b64encode(audio_bytes).decode("utf-8")
                await _send_json(websocket, {"type": "audio", "data": encoded})

            elif event_type == "text":
                await _send_json(websocket, {"type": "text", "data": event["data"]})

            elif event_type == "turn_complete":
                runtime_state["assistant_speaking"] = False
                await _send_json(websocket, {"type": "turn_complete"})
                logger.debug("Turn complete signal sent to browser")

            elif event_type == "interrupted":
                runtime_state["assistant_speaking"] = False
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                lat = _latency_state.get(session_id)
                if lat and lat["last_audio_in"] > 0:
                    delta_ms = (time.time() - lat["last_audio_in"]) * 1000
                    logger.info(
                        "LATENCY session=%s interruption_stop_ms=%.0f",
                        session_id, delta_ms,
                    )
                    lat["awaiting_first_response"] = False
                await _send_json(websocket, {"type": "interrupted"})
                logger.debug("Interrupted signal sent to browser")

            else:
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
