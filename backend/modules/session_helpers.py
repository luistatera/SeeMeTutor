"""
Session lifecycle helpers — heartbeat, activity tracking, speech detection,
away-mode resume, checkpoint decisions, turn management.

Extracted from main.py (Step 4 of refactor).  Every function takes its
dependencies as explicit parameters — no module-level mutable state.
"""

import array
import asyncio
import json
import logging
import math
import time
from typing import Callable, Awaitable, Any

from fastapi import WebSocket, WebSocketDisconnect

from modules.proactive import reset_silence_tracking
from modules.conversation import normalize_for_similarity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Turn text accumulation
# ---------------------------------------------------------------------------

def _append_tutor_turn_part(runtime_state: dict, text: str, *, source: str) -> None:
    clean = str(text or "").strip()
    if not clean:
        return
    turn_parts = runtime_state.setdefault("_tutor_turn_parts", {})
    if not isinstance(turn_parts, dict):
        turn_parts = {}
        runtime_state["_tutor_turn_parts"] = turn_parts
    bucket = turn_parts.setdefault(source, [])
    if not isinstance(bucket, list):
        bucket = []
        turn_parts[source] = bucket
    if bucket and str(bucket[-1]).strip() == clean:
        return
    bucket.append(clean)


def _finalize_tutor_turn(runtime_state: dict) -> dict:
    turn_parts = runtime_state.get("_tutor_turn_parts")
    transcript_parts: list[str] = []
    text_parts: list[str] = []
    if isinstance(turn_parts, dict):
        raw_transcript = turn_parts.get("transcript", [])
        if isinstance(raw_transcript, list):
            transcript_parts = [str(part).strip() for part in raw_transcript if str(part).strip()]
        raw_text = turn_parts.get("text", [])
        if isinstance(raw_text, list):
            text_parts = [str(part).strip() for part in raw_text if str(part).strip()]
    runtime_state["_tutor_turn_parts"] = {"text": [], "transcript": []}
    turn_text = " ".join(transcript_parts or text_parts).strip()
    return {"turn_text": turn_text}


# ---------------------------------------------------------------------------
# Session heartbeat & timer
# ---------------------------------------------------------------------------

async def _session_heartbeat(
    session_id: str,
    runtime_state: dict,
    debug_counters: dict[str, dict],
    debug_logger: logging.Logger,
) -> None:
    """Log session state every 3 seconds for debugging silence issues."""
    d = debug_logger
    prev_counters: dict = {}
    while True:
        await asyncio.sleep(3.0)
        c = debug_counters.get(session_id)
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


# ---------------------------------------------------------------------------
# Student activity tracking
# ---------------------------------------------------------------------------

def _mark_student_activity(runtime_state: dict, *, unlock_turn: bool = False) -> None:
    """Common state reset when the student does something intentional.

    Called from every upstream control-message handler that represents real
    student engagement (speech, button press, source switch, etc.).  Centralises
    the six-line pattern that was copy-pasted across 8+ handlers.

    Args:
        unlock_turn: If True, guarantee exactly one available turn ticket so the
            tutor can respond once to the latest student activity.
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


# ---------------------------------------------------------------------------
# Speech detection (PCM heuristic)
# ---------------------------------------------------------------------------

def _is_probable_speech_pcm16(raw_bytes: bytes) -> bool:
    """Heuristic speech detection for 16kHz PCM16 mono chunks."""
    if not raw_bytes or len(raw_bytes) < 160:
        return False
    try:
        samples = array.array('h', raw_bytes)
        if not samples:
            return False

        peak = max(abs(s) for s in samples)
        sum_squares = sum(s * s for s in samples)
        rms = math.isqrt(sum_squares // len(samples))
    except Exception:
        return False
    # Thresholds raised from rms>=420/peak>=1700 — old values triggered on
    # ambient laptop mic noise, preventing proactive pokes from ever firing.
    return rms >= 800 or peak >= 3000


# ---------------------------------------------------------------------------
# Away-mode resume
# ---------------------------------------------------------------------------

SendJsonFn = Callable[[WebSocket, dict], Awaitable[None]]


async def _resume_from_away(
    websocket: WebSocket,
    runtime_state: dict,
    send_json: SendJsonFn,
) -> None:
    runtime_state["away_mode"] = False
    _mark_student_activity(runtime_state)
    await send_json(websocket, {"type": "assistant_state", "data": {"state": "active", "reason": "resume"}})
    if runtime_state.get("mic_active"):
        resume_message = runtime_state.get("resume_message") or "Welcome back. Let's continue from your last checkpoint."
        await send_json(websocket, {"type": "assistant_prompt", "data": resume_message})


# ---------------------------------------------------------------------------
# Checkpoint decisions
# ---------------------------------------------------------------------------

async def _apply_checkpoint_decision(
    runtime_state: dict,
    session_id: str,
    decision: str,
    firestore_client: Any | None,
) -> dict:
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


# ---------------------------------------------------------------------------
# Resume message builder
# ---------------------------------------------------------------------------

def _merge_resume_message(base_resume_message: str, recall_summary: str) -> str:
    """Attach concise memory summary to the normal resume message."""
    base = str(base_resume_message or "").strip() or "Let's continue where we left off."
    summary = str(recall_summary or "").strip()
    if not summary:
        return base
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "..."
    return f"{base} Quick recap: {summary}"


# ---------------------------------------------------------------------------
# Note dedup signatures
# ---------------------------------------------------------------------------

def _question_answer_signature(question: str, answer: str) -> str:
    q = normalize_for_similarity(question)[:220]
    a = normalize_for_similarity(answer)[:320]
    return f"{q}||{a}"


def _example_signature(example_text: str) -> str:
    return normalize_for_similarity(example_text)[:380]
