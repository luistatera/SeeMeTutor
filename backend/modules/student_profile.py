"""
Student profile & backlog loading — extracted from main.py.

Contains helpers for:
- IP anonymization and input parsing
- ADK session-exists detection
- Building default and Firestore-backed backlog contexts
- Profile picker summaries (Firestore + local fallback)
- In-memory local profile store (seeded from seed_demo_profiles)
- Active-session registration/unregistration
"""

import asyncio
import hashlib
import logging
import time

from modules.tutor_preferences import (
    _default_profile_context,
    _default_search_intent_policy,
    _default_tutor_preferences,
    _merge_search_context_terms,
    _normalize_profile_context,
    _normalize_resource_materials,
    _normalize_search_intent_policy,
    _normalize_tutor_preferences,
    _PLAN_MILESTONE_MIN_DEFAULT,
    _sanitize_long_text,
    _sanitize_text,
    _search_terms_from_profile_context,
    _search_terms_from_setup,
)

logger = logging.getLogger(__name__)

__all__ = [
    "_anonymize_ip",
    "_is_adk_session_exists_error",
    "_parse_int",
    "_safe_order_index",
    "_default_backlog_context",
    "_register_active_student_session",
    "_unregister_active_student_session",
    "_load_backlog_context",
    "_build_profile_summary",
    "_init_local_profiles",
    "_build_local_profile_summary",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _anonymize_ip(ip: str) -> str:
    """Hash an IP address for Firestore storage (never persist raw IPs)."""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _is_adk_session_exists_error(exc: Exception) -> bool:
    """Detect ADK in-memory session ID collision errors across versions."""
    if exc is None:
        return False
    name = type(exc).__name__.strip().lower()
    message = str(exc).strip().lower()
    return name == "alreadyexistserror" or "already exists" in message


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


# ---------------------------------------------------------------------------
# Default backlog context
# ---------------------------------------------------------------------------

def _default_backlog_context(student_id: str, student_name: str = "Student") -> dict:
    search_intent_policy = _default_search_intent_policy()
    tutor_preferences = _default_tutor_preferences()
    profile_context = _default_profile_context()
    return {
        "student_id": student_id,
        "student_name": student_name,
        "track_id": "general-track",
        "track_title": "General Learning",
        "topic_id": "current-topic",
        "topic_title": "Current Topic",
        "topic_status": "in_progress",
        "unresolved_topics": 0,
        "available_topics": [],
        "resume_message": "Let's continue where we left off.",
        "search_intent_policy": search_intent_policy,
        "tutor_preferences": tutor_preferences,
        "profile_context": profile_context,
        "session_setup": {
            "session_goal": "",
            "student_context_text": "",
            "resource_refs": [],
            "resource_materials": [],
            "confirmed": False,
        },
        "plan_bootstrap_required": False,
        "plan_bootstrap_completed": False,
        "plan_milestone_min": _PLAN_MILESTONE_MIN_DEFAULT,
        "plan_milestone_count": 0,
        "plan_fallback_generated": False,
        "plan_bootstrap_source": "",
        "resource_transcript_context": "",
        "resource_transcript_available": False,
        "search_context_terms": [],
        "session_phase": "setup",
    }


# ---------------------------------------------------------------------------
# Active student session registration
# ---------------------------------------------------------------------------

async def _register_active_student_session(
    sessions_dict: dict,
    sessions_lock: asyncio.Lock,
    student_id: str,
    session_id: str,
    websocket,
) -> tuple:
    """Track active sessions per student and return any prior live socket."""
    previous_session_id = None
    previous_websocket = None
    async with sessions_lock:
        previous = sessions_dict.get(student_id)
        if previous:
            previous_session_id = str(previous.get("session_id") or "")
            previous_ws = previous.get("websocket")
            # Check for WebSocket without importing fastapi here
            if previous_ws is not None:
                previous_websocket = previous_ws
        sessions_dict[student_id] = {
            "session_id": session_id,
            "websocket": websocket,
        }
    return previous_session_id, previous_websocket


async def _unregister_active_student_session(
    sessions_dict: dict,
    sessions_lock: asyncio.Lock,
    student_id: str,
    session_id: str,
) -> None:
    """Clear active-session tracking only if this session is still current."""
    async with sessions_lock:
        active = sessions_dict.get(student_id)
        if active and active.get("session_id") == session_id:
            sessions_dict.pop(student_id, None)


# ---------------------------------------------------------------------------
# Firestore backlog context loader
# ---------------------------------------------------------------------------

async def _load_backlog_context(
    firestore_client,
    student_id: str,
    session_data: dict | None = None,
) -> dict | None:
    """Load student backlog context from Firestore and optional selected-session overlay."""
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

    selected_session = session_data if isinstance(session_data, dict) else {}
    selected_setup = selected_session.get("setup") if isinstance(selected_session.get("setup"), dict) else {}
    selected_planning = selected_session.get("planning") if isinstance(selected_session.get("planning"), dict) else {}
    selected_phase = str(selected_session.get("phase") or "setup")
    selected_transcript_context = _sanitize_long_text(
        selected_session.get("resource_transcript_context"),
        max_len=24000,
    )
    selected_resource_materials = _normalize_resource_materials(selected_setup.get("resource_materials"))
    transcript_material_requested = any(
        item.get("kind") == "youtube"
        for item in selected_resource_materials
    )
    transcript_material_ready = any(
        item.get("kind") == "youtube" and item.get("status") == "ready"
        for item in selected_resource_materials
    )
    transcript_available = bool(selected_transcript_context.strip()) or transcript_material_ready
    plan_milestone_min = _parse_int(
        selected_planning.get("milestone_min"),
        _PLAN_MILESTONE_MIN_DEFAULT,
        minimum=1,
        maximum=20,
    )
    plan_milestone_count = _parse_int(
        selected_planning.get("milestone_count"),
        0,
        minimum=0,
        maximum=50,
    )
    plan_fallback_generated = bool(selected_planning.get("fallback_generated"))
    plan_bootstrap_source = _sanitize_text(selected_planning.get("bootstrap_source"), max_len=40)
    if not plan_bootstrap_source and transcript_material_requested:
        plan_bootstrap_source = "youtube_transcript" if transcript_material_ready else "transcript_unavailable"
    if not plan_bootstrap_source:
        plan_bootstrap_source = "session_setup"
    plan_bootstrap_completed = bool(selected_planning.get("bootstrap_completed"))
    if plan_milestone_count >= plan_milestone_min:
        plan_bootstrap_completed = True
    if plan_fallback_generated and not transcript_available:
        plan_bootstrap_completed = True
    if "bootstrap_required" in selected_planning:
        plan_bootstrap_required_config = bool(selected_planning.get("bootstrap_required"))
    else:
        plan_bootstrap_required_config = bool(
            selected_phase in {"setup", "greeting"}
        )
    plan_bootstrap_required = bool(plan_bootstrap_required_config and not plan_bootstrap_completed)
    session_setup = {
        "session_goal": _sanitize_text(selected_setup.get("session_goal")),
        "student_context_text": _sanitize_text(selected_setup.get("student_context_text")),
        "resource_refs": list(selected_setup.get("resource_refs") or []),
        "resource_materials": selected_resource_materials,
        "confirmed": bool(selected_setup.get("confirmed")),
    }

    search_intent_policy = _normalize_search_intent_policy(student_data.get("search_intent_policy"))
    tutor_preferences = _normalize_tutor_preferences(student_data.get("tutor_preferences"))
    profile_context = _normalize_profile_context(student_data.get("profile_context"))
    student_updates: dict = {}
    if student_data.get("search_intent_policy") != search_intent_policy:
        student_updates["search_intent_policy"] = search_intent_policy
    if student_data.get("tutor_preferences") != tutor_preferences:
        student_updates["tutor_preferences"] = tutor_preferences
    if student_data.get("profile_context") != profile_context:
        student_updates["profile_context"] = profile_context

    if not track_rows:
        if student_updates:
            student_updates["updated_at"] = now
            await student_ref.set(student_updates, merge=True)
        context = _default_backlog_context(student_id, student_name)
        context.update({
            "previous_notes": [],
            "resume_message": f"Welcome back, {student_name}. Let's set your first learning track.",
            "search_intent_policy": search_intent_policy,
            "tutor_preferences": tutor_preferences,
            "profile_context": profile_context,
            "session_phase": selected_phase,
            "session_setup": session_setup,
            "plan_bootstrap_required": plan_bootstrap_required,
            "plan_bootstrap_completed": plan_bootstrap_completed,
            "plan_milestone_min": plan_milestone_min,
            "plan_milestone_count": plan_milestone_count,
            "plan_fallback_generated": plan_fallback_generated,
            "plan_bootstrap_source": plan_bootstrap_source,
            "resource_transcript_context": selected_transcript_context,
            "resource_transcript_available": transcript_available,
        })
        session_track_id = _sanitize_text(selected_session.get("track_id"), max_len=80)
        session_track_title = _sanitize_text(selected_session.get("track_title"), max_len=120)
        session_topic_id = _sanitize_text(selected_session.get("topic_id"), max_len=80)
        session_topic_title = _sanitize_text(selected_session.get("topic_title"), max_len=140)
        if session_track_id:
            context["track_id"] = session_track_id
        if session_track_title:
            context["track_title"] = session_track_title
        if session_topic_id:
            context["topic_id"] = session_topic_id
        if session_topic_title:
            context["topic_title"] = session_topic_title
        context["search_context_terms"] = _merge_search_context_terms(
            _search_terms_from_profile_context(profile_context),
            _search_terms_from_setup(context.get("session_setup")),
            [context.get("track_title"), context.get("topic_title")],
        )
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
            "context_query": str(topic_data.get("context_query") or "").strip(),
            "context_summary": str(topic_data.get("context_summary") or "").strip(),
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
            "previous_notes": [],
            "track_id": active_track_id,
            "track_title": str(active_track_data.get("title") or active_track_id),
            "resume_message": f"Welcome back, {student_name}. Let's choose your next topic.",
            "search_intent_policy": search_intent_policy,
            "tutor_preferences": tutor_preferences,
            "profile_context": profile_context,
            "session_phase": selected_phase,
            "session_setup": session_setup,
            "plan_bootstrap_required": plan_bootstrap_required,
            "plan_bootstrap_completed": plan_bootstrap_completed,
            "plan_milestone_min": plan_milestone_min,
            "plan_milestone_count": plan_milestone_count,
            "plan_fallback_generated": plan_fallback_generated,
            "plan_bootstrap_source": plan_bootstrap_source,
            "resource_transcript_context": selected_transcript_context,
            "resource_transcript_available": transcript_available,
        })
        session_track_id = _sanitize_text(selected_session.get("track_id"), max_len=80)
        session_track_title = _sanitize_text(selected_session.get("track_title"), max_len=120)
        session_topic_id = _sanitize_text(selected_session.get("topic_id"), max_len=80)
        session_topic_title = _sanitize_text(selected_session.get("topic_title"), max_len=140)
        if session_track_id:
            context["track_id"] = session_track_id
        if session_track_title:
            context["track_title"] = session_track_title
        if session_topic_id:
            context["topic_id"] = session_topic_id
        if session_topic_title:
            context["topic_title"] = session_topic_title
        context["search_context_terms"] = _merge_search_context_terms(
            _search_terms_from_profile_context(profile_context),
            _search_terms_from_setup(context.get("session_setup")),
            [context.get("track_title"), context.get("topic_title")],
        )
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

    session_track_id = _sanitize_text(selected_session.get("track_id"), max_len=80)
    session_track_title = _sanitize_text(selected_session.get("track_title"), max_len=120)
    session_topic_id = _sanitize_text(selected_session.get("topic_id"), max_len=80)
    session_topic_title = _sanitize_text(selected_session.get("topic_title"), max_len=140)
    resolved_track_id = session_track_id or active_track_id
    resolved_track_title = session_track_title or str(active_track_data.get("title") or active_track_id)
    resolved_topic_id = session_topic_id or str(active_topic.get("id") or "")
    resolved_topic_title = session_topic_title or topic_title
    search_context_terms = _merge_search_context_terms(
        _search_terms_from_profile_context(profile_context),
        _search_terms_from_setup(session_setup),
        [resolved_track_title, resolved_topic_title],
    )
    if plan_bootstrap_required:
        previous_notes = []
        resume_message = (
            f"Welcome back, {student_name}. Let's build a 0-to-hero milestone plan "
            "from your shared resource before we start solving."
        )

    return {
        "student_id": student_id,
        "student_name": student_name,
        "track_id": resolved_track_id,
        "track_title": resolved_track_title,
        "topic_id": resolved_topic_id,
        "topic_title": resolved_topic_title,
        "topic_status": topic_status,
        "unresolved_topics": unresolved_topics,
        "available_topics": [
            {"id": row["id"], "title": row["title"], "status": row["status"]}
            for row in topic_rows
        ],
        "previous_notes": previous_notes,
        "resume_message": resume_message,
        "search_intent_policy": search_intent_policy,
        "tutor_preferences": tutor_preferences,
        "profile_context": profile_context,
        "session_phase": selected_phase,
        "session_setup": session_setup,
        "plan_bootstrap_required": plan_bootstrap_required,
        "plan_bootstrap_completed": plan_bootstrap_completed,
        "plan_milestone_min": plan_milestone_min,
        "plan_milestone_count": plan_milestone_count,
        "plan_fallback_generated": plan_fallback_generated,
        "plan_bootstrap_source": plan_bootstrap_source,
        "resource_transcript_context": selected_transcript_context,
        "resource_transcript_available": transcript_available,
        "search_context_terms": search_context_terms,
        "topic_context_query": active_topic.get("context_query", ""),
        "topic_context_summary": active_topic.get("context_summary", ""),
    }


# ---------------------------------------------------------------------------
# Firestore profile summary (for profile picker)
# ---------------------------------------------------------------------------

async def _build_profile_summary(student_snapshot) -> dict:
    """Build lightweight profile data for profile picker UI."""
    student_data = student_snapshot.to_dict() or {}
    student_id = student_snapshot.id
    student_ref = student_snapshot.reference
    student_name = str(student_data.get("name") or student_id)
    tutor_preferences = _normalize_tutor_preferences(student_data.get("tutor_preferences"))
    profile_context = _normalize_profile_context(student_data.get("profile_context"))
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

    study_subject = str(profile_context.get("study_subject") or "").strip()

    return {
        "id": student_id,
        "name": student_name,
        "track": chosen_track["title"] if chosen_track else "General Learning",
        "focus": focus,
        "current_topic": topic_title,
        "study_subject": study_subject,
        "tutor_preferences": tutor_preferences,
        "profile_context": profile_context,
    }


# ---------------------------------------------------------------------------
# In-memory local profile store (seed-data fallback for local dev)
# ---------------------------------------------------------------------------

def _init_local_profiles(
    local_profiles: dict,
    local_tracks: dict,
    local_topics: dict,
    seed_profiles: list,
) -> None:
    """Build in-memory profile store from seed data."""
    for profile in seed_profiles:
        sid = profile["student_id"]
        local_profiles[sid] = dict(profile["student"])
        local_tracks[sid] = []
        local_topics[sid] = {}
        for track_def in profile.get("tracks", []):
            tid = track_def["track_id"]
            local_tracks[sid].append({"id": tid, **track_def["track"]})
            local_topics[sid][tid] = [
                {"id": t["topic_id"], "title": t["title"], "status": t["status"],
                 "order_index": t["order_index"], "context_query": t["context_query"]}
                for t in track_def.get("topics", [])
            ]
    logger.info("Local profile store: %d profiles loaded from seed data", len(local_profiles))


def _build_local_profile_summary(
    student_id: str,
    local_profiles: dict,
    local_tracks: dict,
    local_topics: dict,
) -> dict | None:
    """Build profile summary from local store (mirrors _build_profile_summary)."""
    student_data = local_profiles.get(student_id)
    if not student_data:
        return None

    student_name = str(student_data.get("name") or student_id)
    tutor_preferences = _normalize_tutor_preferences(student_data.get("tutor_preferences"))
    profile_context = _normalize_profile_context(student_data.get("profile_context"))
    active_track_id = str(student_data.get("active_track_id") or "").strip()

    track_rows = local_tracks.get(student_id, [])
    sorted_tracks = sorted(
        track_rows,
        key=lambda row: (0 if row["id"] == active_track_id else 1, row.get("title", "").lower()),
    )
    chosen_track = sorted_tracks[0] if sorted_tracks else None

    topic_title = ""
    if chosen_track:
        topic_rows = local_topics.get(student_id, {}).get(chosen_track["id"], [])
        sorted_topics = sorted(topic_rows, key=lambda r: (r.get("order_index", 9999), r.get("title", "")))
        active_topic = next((r for r in sorted_topics if r.get("status") != "mastered"), None)
        if active_topic is None and sorted_topics:
            active_topic = sorted_topics[0]
        if active_topic:
            topic_title = active_topic["title"]

    focus = ""
    if chosen_track and chosen_track.get("goal"):
        focus = chosen_track["goal"]
    elif topic_title:
        focus = f"Continue with {topic_title}"
    if not focus:
        focus = "Continue learning"

    study_subject = str(profile_context.get("study_subject") or "").strip()

    return {
        "id": student_id,
        "name": student_name,
        "track": chosen_track["title"] if chosen_track else "General Learning",
        "focus": focus,
        "current_topic": topic_title,
        "study_subject": study_subject,
        "tutor_preferences": tutor_preferences,
        "profile_context": profile_context,
    }
