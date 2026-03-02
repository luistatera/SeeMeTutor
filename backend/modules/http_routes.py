"""HTTP route handlers for SeeMe Tutor.

Extracted from main.py to keep the WebSocket handler focused on real-time
orchestration.  All endpoints are registered on a FastAPI ``APIRouter`` that
main.py includes via ``app.include_router(router)``.

Shared runtime state (Firestore client, rate limiters, security headers,
local in-memory stores, active-session registry) is accessed through
``request.app.state`` so this module stays free of mutable module-level
globals.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from modules.security import extract_client_ip
from modules.tutor_preferences import (
    _PLAN_MILESTONE_MIN_DEFAULT,
    _normalize_tutor_preferences,
    _normalize_profile_context,
    _normalize_resource_materials,
    _sanitize_long_text,
    _sanitize_text,
)
from modules.student_profile import (
    _build_profile_summary,
    _build_local_profile_summary as _build_local_profile_summary_impl,
    _load_backlog_context as _load_backlog_context_impl,
)
from modules.resource_ingestion import ingest_youtube_transcripts

if TYPE_CHECKING:
    pass

__all__ = ["router", "http_security_middleware"]

logger = logging.getLogger(__name__)

# Re-compiled here so that the routes module is self-contained for validation.
# main.py also keeps these patterns (used by the WS handler).
_STUDENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
_SESSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,127}$")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _should_rate_limit_http(path: str) -> bool:
    """Return True if *path* should be subject to HTTP rate limiting."""
    if path == "/health":
        return False
    if path.startswith("/static/"):
        return False
    return True


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

async def http_security_middleware(request: Request, call_next):
    """Apply rate limiting and attach security headers to every response."""
    path = request.url.path
    if _should_rate_limit_http(path):
        client_ip = extract_client_ip(
            request.headers.get("x-forwarded-for"),
            request.client.host if request.client else None,
        )
        http_rate_limiter = request.app.state.http_rate_limiter
        rate_limit_max = request.app.state.http_rate_limit_max
        rate_limit_window = request.app.state.http_rate_limit_window_s
        allowed = await http_rate_limiter.allow(
            f"http:{client_ip}",
            limit=rate_limit_max,
            window_seconds=rate_limit_window,
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please retry shortly."},
                headers={"Retry-After": str(rate_limit_window)},
            )

    response = await call_next(request)
    security_headers: dict = request.app.state.security_headers
    for name, value in security_headers.items():
        response.headers.setdefault(name, value)
    return response


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

@router.get("/", include_in_schema=False)
async def serve_index(request: Request) -> FileResponse:
    """Serve the frontend single-page application."""
    frontend_dir = request.app.state.frontend_dir
    index_path = frontend_dir / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_path))


@router.get("/health")
async def health_check() -> dict:
    """Liveness probe for Cloud Run."""
    return {"status": "ok", "service": "seeme-tutor"}


@router.get("/api/profiles")
async def list_profiles(request: Request) -> dict:
    """Return profile cards from Firestore (or local store as fallback)."""
    fc = request.app.state.firestore_client

    if fc:
        profiles: list[dict] = []
        async for student_snapshot in fc.collection("students").stream():
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

    # Fallback: serve from local in-memory store (seed data)
    local_profiles = request.app.state.local_profiles
    local_tracks = request.app.state.local_tracks
    local_topics = request.app.state.local_topics

    profiles = []
    for sid in local_profiles:
        summary = _build_local_profile_summary_impl(sid, local_profiles, local_tracks, local_topics)
        if summary and summary.get("id"):
            profiles.append(summary)
    profiles.sort(key=lambda p: (str(p.get("name", "")).lower(), str(p.get("id", ""))))
    return {"profiles": profiles}


@router.post("/api/profiles/{student_id}/preferences")
async def save_profile_preferences(student_id: str, request: Request) -> dict:
    """Persist tutor preferences for one student profile."""
    normalized_student_id = str(student_id or "").strip().lower()
    if not _STUDENT_ID_PATTERN.match(normalized_student_id):
        raise HTTPException(status_code=400, detail="Invalid profile identifier.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload.")

    raw_preferences = payload.get("tutor_preferences", payload)
    if raw_preferences is None:
        raw_preferences = {}
    if not isinstance(raw_preferences, dict):
        raise HTTPException(status_code=400, detail="tutor_preferences must be an object.")

    fc = request.app.state.firestore_client

    if fc:
        student_ref = fc.collection("students").document(normalized_student_id)
        student_snapshot = await student_ref.get()
        if not student_snapshot.exists:
            raise HTTPException(status_code=404, detail="Profile not found.")
        student_data = student_snapshot.to_dict() or {}

        existing_preferences = _normalize_tutor_preferences(student_data.get("tutor_preferences"))
        normalized_preferences = _normalize_tutor_preferences(raw_preferences, existing_preferences)

        await student_ref.set(
            {"tutor_preferences": normalized_preferences, "updated_at": time.time()},
            merge=True,
        )
    else:
        local_profiles = request.app.state.local_profiles
        student_data = local_profiles.get(normalized_student_id)
        if not student_data:
            raise HTTPException(status_code=404, detail="Profile not found.")
        existing_preferences = _normalize_tutor_preferences(student_data.get("tutor_preferences"))
        normalized_preferences = _normalize_tutor_preferences(raw_preferences, existing_preferences)
        student_data["tutor_preferences"] = normalized_preferences

    return {
        "student_id": normalized_student_id,
        "tutor_preferences": normalized_preferences,
    }


@router.post("/api/profiles/{student_id}/context")
async def save_profile_context(student_id: str, request: Request) -> dict:
    """Persist learner grounding context for one student profile."""
    normalized_student_id = str(student_id or "").strip().lower()
    if not _STUDENT_ID_PATTERN.match(normalized_student_id):
        raise HTTPException(status_code=400, detail="Invalid profile identifier.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload.")

    raw_context = payload.get("profile_context", payload)
    if raw_context is None:
        raw_context = {}
    if not isinstance(raw_context, dict):
        raise HTTPException(status_code=400, detail="profile_context must be an object.")

    fc = request.app.state.firestore_client

    if fc:
        student_ref = fc.collection("students").document(normalized_student_id)
        student_snapshot = await student_ref.get()
        if not student_snapshot.exists:
            raise HTTPException(status_code=404, detail="Profile not found.")
        student_data = student_snapshot.to_dict() or {}

        existing_context = _normalize_profile_context(student_data.get("profile_context"))
        normalized_context = _normalize_profile_context(raw_context, existing_context)

        await student_ref.set(
            {"profile_context": normalized_context, "updated_at": time.time()},
            merge=True,
        )
    else:
        local_profiles = request.app.state.local_profiles
        student_data = local_profiles.get(normalized_student_id)
        if not student_data:
            raise HTTPException(status_code=404, detail="Profile not found.")
        existing_context = _normalize_profile_context(student_data.get("profile_context"))
        normalized_context = _normalize_profile_context(raw_context, existing_context)
        student_data["profile_context"] = normalized_context

    return {
        "student_id": normalized_student_id,
        "profile_context": normalized_context,
    }


@router.get("/api/profiles/{student_id}/sessions")
async def list_profile_sessions(student_id: str, request: Request) -> dict:
    """List sessions for one student profile."""
    normalized_student_id = str(student_id or "").strip().lower()
    if not _STUDENT_ID_PATTERN.match(normalized_student_id):
        raise HTTPException(status_code=400, detail="Invalid profile identifier.")

    status_filter = str(request.query_params.get("status", "open") or "open").strip().lower()
    if status_filter not in {"open", "mastered", "all"}:
        raise HTTPException(status_code=400, detail="status must be one of: open, mastered, all")

    try:
        limit = int(request.query_params.get("limit", "20"))
    except ValueError:
        raise HTTPException(status_code=400, detail="limit must be an integer")
    limit = max(1, min(50, limit))

    fc = request.app.state.firestore_client

    if fc:
        rows: list[dict] = []
        async for snap in fc.collection("sessions").stream():
            data = snap.to_dict() or {}
            if str(data.get("student_id") or "").strip().lower() != normalized_student_id:
                continue
            state = str(data.get("status") or "open").strip().lower()
            if status_filter != "all" and state != status_filter:
                continue
            rows.append(
                {
                    "session_id": snap.id,
                    "status": state,
                    "phase": str(data.get("phase") or "setup"),
                    "topic_id": str(data.get("topic_id") or ""),
                    "topic_title": str(data.get("topic_title") or ""),
                    "track_id": str(data.get("track_id") or ""),
                    "track_title": str(data.get("track_title") or ""),
                    "started_at": float(data.get("started_at") or 0.0),
                    "updated_at": float(data.get("updated_at") or data.get("started_at") or 0.0),
                    "ended_reason": data.get("ended_reason"),
                    "setup": data.get("setup") if isinstance(data.get("setup"), dict) else {},
                }
            )
        rows.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
        return {"sessions": rows[:limit]}

    # Local fallback: filter in-memory sessions
    local_sessions = request.app.state.local_sessions
    rows = []
    for sid, data in local_sessions.items():
        if str(data.get("student_id") or "").strip().lower() != normalized_student_id:
            continue
        state = str(data.get("status") or "open").strip().lower()
        if status_filter != "all" and state != status_filter:
            continue
        rows.append({
            "session_id": sid,
            "status": state,
            "phase": str(data.get("phase") or "setup"),
            "topic_id": str(data.get("topic_id") or ""),
            "topic_title": str(data.get("topic_title") or ""),
            "track_id": str(data.get("track_id") or ""),
            "track_title": str(data.get("track_title") or ""),
            "started_at": float(data.get("started_at") or 0.0),
            "updated_at": float(data.get("updated_at") or data.get("started_at") or 0.0),
            "ended_reason": data.get("ended_reason"),
            "setup": data.get("setup") if isinstance(data.get("setup"), dict) else {},
        })
    rows.sort(key=lambda item: float(item.get("updated_at") or 0.0), reverse=True)
    return {"sessions": rows[:limit]}


@router.post("/api/profiles/{student_id}/sessions")
async def create_profile_session(student_id: str, request: Request) -> dict:
    """Create a new tutoring session placeholder for a student profile."""
    normalized_student_id = str(student_id or "").strip().lower()
    if not _STUDENT_ID_PATTERN.match(normalized_student_id):
        raise HTTPException(status_code=400, detail="Invalid profile identifier.")

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload.")

    fc = request.app.state.firestore_client
    local_profiles = request.app.state.local_profiles
    local_tracks = request.app.state.local_tracks
    local_topics = request.app.state.local_topics
    local_sessions = request.app.state.local_sessions

    # Verify student exists
    if fc:
        student_ref = fc.collection("students").document(normalized_student_id)
        student_snapshot = await student_ref.get()
        if not student_snapshot.exists:
            raise HTTPException(status_code=404, detail="Profile not found.")
    elif normalized_student_id not in local_profiles:
        raise HTTPException(status_code=404, detail="Profile not found.")

    # Load backlog context (Firestore) or build local fallback
    if fc:
        base_context = await _load_backlog_context_impl(fc, normalized_student_id)
        if not base_context:
            raise HTTPException(status_code=400, detail="Could not load student context.")
    else:
        # Build lightweight context from local store
        local_student = local_profiles[normalized_student_id]
        local_track_list = local_tracks.get(normalized_student_id, [])
        active_track_id = str(local_student.get("active_track_id") or "").strip()
        chosen = next((t for t in local_track_list if t["id"] == active_track_id), local_track_list[0] if local_track_list else None)
        local_topic_rows = local_topics.get(normalized_student_id, {}).get(chosen["id"] if chosen else "", [])
        active_topic = next((t for t in local_topic_rows if t.get("status") != "mastered"), local_topic_rows[0] if local_topic_rows else None)
        base_context = {
            "track_id": chosen["id"] if chosen else "",
            "track_title": chosen.get("title", "") if chosen else "",
            "topic_id": active_topic["id"] if active_topic else "current-topic",
            "topic_title": active_topic.get("title", "Current Topic") if active_topic else "Current Topic",
        }

    now = time.time()
    session_id = str(uuid.uuid4())
    setup_payload = payload.get("setup") if isinstance(payload.get("setup"), dict) else {}
    resource_refs = [
        _sanitize_text(item, max_len=400)
        for item in list(setup_payload.get("resource_refs") or [])
        if _sanitize_text(item, max_len=400)
    ][:10]
    resource_materials, transcript_context = await ingest_youtube_transcripts(resource_refs)
    normalized_resource_materials = _normalize_resource_materials(resource_materials)
    youtube_materials = [
        item for item in normalized_resource_materials if item.get("kind") == "youtube"
    ]
    youtube_requested = len(youtube_materials)
    youtube_imported = sum(1 for item in youtube_materials if item.get("status") == "ready")
    youtube_failed = sum(
        1 for item in youtube_materials if item.get("status") in {"unavailable", "error"}
    )
    plan_bootstrap_required = True
    plan_bootstrap_completed = False
    if youtube_imported > 0:
        plan_bootstrap_source = "youtube_transcript"
    elif youtube_requested > 0:
        plan_bootstrap_source = "transcript_unavailable"
    else:
        plan_bootstrap_source = "session_setup"
    session_setup = {
        "session_goal": _sanitize_text(setup_payload.get("session_goal")),
        "student_context_text": _sanitize_text(setup_payload.get("student_context_text")),
        "resource_refs": resource_refs,
        "resource_materials": normalized_resource_materials,
        "confirmed": bool(setup_payload.get("confirmed", False)),
        "confirmed_at": None,
    }

    track_id = _sanitize_text(payload.get("track_id"), max_len=80) or str(base_context.get("track_id") or "")
    track_title = _sanitize_text(payload.get("track_title"), max_len=120) or str(base_context.get("track_title") or track_id)
    topic_id = _sanitize_text(payload.get("topic_id"), max_len=80) or str(base_context.get("topic_id") or "current-topic")
    topic_title = _sanitize_text(payload.get("topic_title"), max_len=140) or str(base_context.get("topic_title") or "Current Topic")

    doc = {
        "session_id": session_id,
        "student_id": normalized_student_id,
        "status": "open",
        "phase": "setup",
        "started_at": now,
        "updated_at": now,
        "closed_at": None,
        "ended_reason": None,
        "duration_seconds": None,
        "consent_given": False,
        "track_id": track_id,
        "track_title": track_title,
        "topic_id": topic_id,
        "topic_title": topic_title,
        "resource_transcript_context": _sanitize_long_text(transcript_context, max_len=24000),
        "setup": session_setup,
        "capture": {
            "source": "none",
            "summary_text": "",
            "artifacts_count": 0,
            "confirmed": False,
            "confirmed_at": None,
        },
        "planning": {
            "bootstrap_required": plan_bootstrap_required,
            "bootstrap_completed": plan_bootstrap_completed,
            "milestone_min": _PLAN_MILESTONE_MIN_DEFAULT,
            "milestone_count": 0,
            "fallback_generated": False,
            "fallback_reason": "",
            "bootstrap_source": plan_bootstrap_source,
        },
        "mastery": {
            "state": "not_started",
            "last_proposed_at": None,
            "last_evaluated_at": None,
            "last_outcome": None,
            "approved_at": None,
        },
    }

    if fc:
        await fc.collection("sessions").document(session_id).set(doc, merge=True)
    else:
        local_sessions[session_id] = doc

    return {
        "session_id": session_id,
        "status": "open",
        "phase": "setup",
        "topic_id": topic_id,
        "topic_title": topic_title,
        "track_id": track_id,
        "track_title": track_title,
        "setup": session_setup,
        "resource_ingestion": {
            "youtube_requested": youtube_requested,
            "youtube_imported": youtube_imported,
            "youtube_failed": youtube_failed,
        },
    }


@router.delete("/api/profiles/{student_id}/sessions/{session_id}")
async def delete_profile_session(student_id: str, session_id: str, request: Request) -> dict:
    """Delete one session for a student profile."""
    normalized_student_id = str(student_id or "").strip().lower()
    if not _STUDENT_ID_PATTERN.match(normalized_student_id):
        raise HTTPException(status_code=400, detail="Invalid profile identifier.")

    normalized_session_id = str(session_id or "").strip().lower()
    if not _SESSION_ID_PATTERN.match(normalized_session_id):
        raise HTTPException(status_code=400, detail="Invalid session identifier.")

    active_student_sessions_lock = request.app.state.active_student_sessions_lock
    active_student_sessions = request.app.state.active_student_sessions

    async with active_student_sessions_lock:
        active = active_student_sessions.get(normalized_student_id)
        if active and str(active.get("session_id") or "").strip().lower() == normalized_session_id:
            raise HTTPException(status_code=409, detail="Cannot delete an active live session.")

    fc = request.app.state.firestore_client

    if fc:
        session_ref = fc.collection("sessions").document(normalized_session_id)
        snapshot = await session_ref.get()
        if not snapshot.exists:
            raise HTTPException(status_code=404, detail="Session not found.")
        session_data = snapshot.to_dict() or {}
        session_student_id = str(session_data.get("student_id") or "").strip().lower()
        if session_student_id != normalized_student_id:
            raise HTTPException(status_code=404, detail="Session not found for this profile.")
        await session_ref.delete()
    else:
        local_sessions = request.app.state.local_sessions
        session_data = local_sessions.get(normalized_session_id)
        if not session_data:
            raise HTTPException(status_code=404, detail="Session not found.")
        if str(session_data.get("student_id") or "").strip().lower() != normalized_student_id:
            raise HTTPException(status_code=404, detail="Session not found for this profile.")
        del local_sessions[normalized_session_id]

    return {"deleted": True, "session_id": normalized_session_id}
