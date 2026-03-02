"""
WebSocket bridge handlers — forward_to_gemini, forward_to_client, retry logic.

Extracted from main.py (Step 7 of refactor).  Every function takes its
dependencies as explicit parameters — no module-level mutable state.
"""

import asyncio
import base64
import binascii
import json
import logging
import time
from typing import Any, Callable, Awaitable

from fastapi import WebSocket, WebSocketDisconnect

from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.genai import types

from modules.conversation import (
    expects_student_reply,
    is_near_duplicate,
    is_question_like_turn,
    is_student_question,
    is_study_related_question,
)
from modules.guardrails import (
    check_student_input,
    check_tutor_output,
    select_reinforcement,
    record_guardrail_event,
    record_reinforcement,
)
from modules.live_session import (
    compute_retry_backoff,
    extract_total_token_estimate,
)
from modules.memory_manager import append_transcript_piece
from modules.note_storage import (
    _maybe_store_question_answer_note,
    _maybe_store_example_note,
)
from modules.persistence import _persist_memory_checkpoint
from modules.proactive import (
    reset_silence_tracking,
    sanitize_tutor_output,
)
from modules.screen_share import (
    get_switch_prompt,
    STOP_SHARING_PROMPT,
    SOURCE_SWITCH_COOLDOWN_S,
)
from modules.session_helpers import (
    _append_tutor_turn_part,
    _finalize_tutor_turn,
    _mark_student_activity,
    _mark_proactive_response_seen,
    _is_probable_speech_pcm16,
    _resume_from_away,
    _apply_checkpoint_decision,
)
from modules.tutor_preferences import (
    PACE_CONTROL_INSTRUCTIONS,
    ANTI_REPEAT_CONTROL_PROMPT,
    ANTI_QUESTION_LOOP_CONTROL_PROMPT,
    ANTI_QUESTION_LOOP_ESCALATED_PROMPT,
    _normalize_tutor_preferences,
    _build_tutor_preferences_control_prompt,
)

logger = logging.getLogger(__name__)

SendJsonFn = Callable[[WebSocket, dict], Awaitable[None]]
BuildRunConfigFn = Callable[[], tuple[RunConfig, dict]]


# ---------------------------------------------------------------------------
# Exception for student-initiated session end
# ---------------------------------------------------------------------------

class _StudentEndedSession(Exception):
    """Raised by _forward_to_gemini when the student explicitly ends the session."""


# ---------------------------------------------------------------------------
# JSON send helper
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Browser → Gemini
# ---------------------------------------------------------------------------

async def _forward_to_gemini(
    websocket: WebSocket,
    queue: LiveRequestQueue,
    session_id: str,
    runtime_state: dict,
    *,
    firestore_client: Any | None,
    latency_state: dict[str, dict],
    debug_counters: dict[str, dict],
    debug_logger: logging.Logger,
    report: Any | None = None,
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
    _msg_type_counts: dict[str, int] = {}
    _msg_diag_last = time.time()

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

            # Diagnostic: count every message type received
            _msg_type_counts[msg_type] = _msg_type_counts.get(msg_type, 0) + 1
            _now_diag = time.time()
            if _now_diag - _msg_diag_last >= 10:
                logger.info("Session %s WS-IN types (last 10s): %s", session_id, _msg_type_counts)
                _msg_type_counts.clear()
                _msg_diag_last = _now_diag

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
                    await _resume_from_away(websocket, runtime_state, _send_json)
                continue
            if msg_type == "mic_start":
                now = time.time()
                runtime_state["mic_active"] = True
                runtime_state["mic_opened_at"] = now
                runtime_state["last_user_activity_at"] = now
                runtime_state["idle_stage"] = 0
                runtime_state["proactive_waiting_for_student"] = False
                # If conversation already started (e.g. proactive poke fired
                # while camera was active), don't reset — keep the context.
                if not runtime_state.get("conversation_started"):
                    runtime_state["mic_kickoff_sent"] = False
                    runtime_state["conversation_started"] = False
                    reset_silence_tracking(runtime_state)
                else:
                    # Conversation already active — mic opening is just adding
                    # a new modality, not restarting.  Mark kickoff as done to
                    # prevent the redundant "Let's begin" message.
                    runtime_state["mic_kickoff_sent"] = True
                # try:
                #     queue.send_activity_start()
                # except Exception:
                #     logger.debug("Session %s: failed to send activity_start", session_id, exc_info=True)
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
                    await _resume_from_away(websocket, runtime_state, _send_json)
                continue
            if msg_type == "checkpoint_decision":
                decision = str(message.get("decision", "")).strip().lower()
                result = await _apply_checkpoint_decision(runtime_state, session_id, decision, firestore_client)
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
            if msg_type == "tutor_preferences_update":
                raw_preferences = message.get("data", {})
                if not isinstance(raw_preferences, dict):
                    logger.warning("Session %s: invalid tutor_preferences_update payload", session_id)
                    continue
                current_preferences = _normalize_tutor_preferences(runtime_state.get("tutor_preferences"))
                updated_preferences = _normalize_tutor_preferences(raw_preferences, current_preferences)
                runtime_state["tutor_preferences"] = updated_preferences
                _mark_student_activity(runtime_state, unlock_turn=True)
                control_prompt = _build_tutor_preferences_control_prompt(updated_preferences)
                try:
                    queue.send_content(
                        types.Content(role="user", parts=[types.Part(text=control_prompt)]),
                    )
                    runtime_state["last_hidden_prompt_at"] = time.time()
                except Exception:
                    logger.warning(
                        "Session %s: failed to forward tutor preferences update",
                        session_id,
                        exc_info=True,
                    )
                await _send_json(websocket, {"type": "tutor_preferences_ack", "data": updated_preferences})
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
                        types.Content(role="user", parts=[types.Part(text=instruction)]),
                    )
                except Exception:
                    logger.warning("Session %s: failed to forward speech pace command", session_id, exc_info=True)
                continue
            if msg_type == "barge_in":
                now = time.time()
                was_speaking = bool(runtime_state.get("assistant_speaking"))
                if was_speaking:
                    _mark_student_activity(runtime_state, unlock_turn=True)
                    runtime_state["_student_has_spoken"] = True
                    runtime_state["assistant_speaking"] = False
                    await _send_json(websocket, {"type": "interrupted"})
                else:
                    # Stale/echo VAD spikes can emit barge_in even when the tutor is
                    # already silent. Treat as activity only; do not unlock tickets.
                    runtime_state["last_user_activity_at"] = now
                    runtime_state["idle_stage"] = 0
                    reset_silence_tracking(runtime_state)
                    debug_logger.debug(
                        "BARGE_IN_IGNORED sid=%s (assistant not speaking)",
                        session_id[:8],
                    )
                # Record barge-in in test report
                if report:
                    report.record_barge_in(while_speaking=was_speaking)
                continue
            if msg_type == "command_event":
                from modules.persistence import _log_command_event
                await _log_command_event(session_id, runtime_state, message.get("data", {}), firestore_client)
                continue
            if msg_type in ("mic_stop", "camera_off"):
                if msg_type == "mic_stop":
                    runtime_state["mic_active"] = False
                    runtime_state["mic_opened_at"] = None
                    runtime_state["mic_kickoff_sent"] = False
                    runtime_state["idle_stage"] = 0
                    # try:
                    #     queue.send_activity_end()
                    # except Exception:
                    #     logger.debug("Session %s: failed to send activity_end", session_id, exc_info=True)
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
                            types.Content(role="user", parts=[types.Part(text=prompt)]),
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
                        types.Content(role="user", parts=[types.Part(text=STOP_SHARING_PROMPT)]),
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
                if audio_chunks_sent == 0:
                    logger.info("Session %s: FIRST AUDIO CHUNK received from browser", session_id)
                # Audio heuristic: grant a ticket when speech-like audio is detected,
                # but do NOT open the greeting gate here. Only input_transcription
                # or barge_in should set _student_has_spoken, because the PCM
                # heuristic fires on ambient noise and defeats the greeting gate.
                if (
                    runtime_state.get("_greeting_delivered")
                    and not runtime_state.get("_student_has_spoken")
                    and _is_probable_speech_pcm16(raw_bytes)
                ):
                    # Do not unlock turn tickets from raw PCM heuristics.
                    # We only unlock on validated non-echo transcripts (or
                    # explicit controls) to prevent duplicate greeting loops.
                    logger.debug("Speech-like audio detected — waiting for validated transcript before granting ticket")
                # Raw audio chunks are continuous (~15/sec) even during silence.
                # When speech-like audio IS detected, reset the idle timer
                # (for idle/away detection) but do NOT reset proactive silence
                # tracking.  Proactive pokes should only be reset by confirmed
                # student speech (input_transcription), not raw PCM heuristics
                # that may trigger on ambient mic noise.
                # Rate-limit to once per second to avoid excessive resets.
                if _is_probable_speech_pcm16(raw_bytes):
                    last_speech_reset = float(runtime_state.get("_last_speech_audio_reset", 0.0))
                    if (now - last_speech_reset) >= 1.0:
                        runtime_state["last_user_activity_at"] = now
                        runtime_state["idle_stage"] = 0
                        # NOTE: intentionally NOT calling reset_silence_tracking()
                        # here. The proactive silence counter must only reset on
                        # confirmed student input (transcription), not on PCM
                        # heuristics that fire on ambient noise.
                        runtime_state["_last_speech_audio_reset"] = now
                lat = latency_state.get(session_id)
                if lat is not None:
                    lat["last_audio_in"] = now
                    lat["awaiting_first_response"] = True
                runtime_state["latency_last_audio_in_at"] = now
                dc = debug_counters.get(session_id)
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
                dc = debug_counters.get(session_id)
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


# ---------------------------------------------------------------------------
# ADK stream retry wrapper
# ---------------------------------------------------------------------------

async def _iter_runner_events_with_retry(
    websocket: WebSocket,
    adk_runner: Runner,
    user_id: str,
    session_id: str,
    live_queue: LiveRequestQueue,
    *,
    build_session_run_config: BuildRunConfigFn,
    adk_stream_max_retries: int,
    adk_stream_retry_backoff_s: float,
    runtime_state: dict | None = None,
    report: Any | None = None,
):
    """Yield ADK stream events with bounded retries."""
    attempt = 0
    while True:
        sent_reconnected = False
        active_run_config = None
        if isinstance(runtime_state, dict):
            active_run_config = runtime_state.get("session_run_config")
        if active_run_config is None:
            active_run_config, _ = build_session_run_config()
        try:
            async for event in adk_runner.run_live(
                user_id=user_id,
                session_id=session_id,
                live_request_queue=live_queue,
                run_config=active_run_config,
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
            if attempt >= adk_stream_max_retries:
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
                adk_stream_max_retries,
                exc,
            )
            await _send_json(
                websocket,
                {
                    "type": "assistant_state",
                    "data": {"state": "reconnecting", "reason": "stream_error", "attempt": attempt},
                },
            )
            backoff_seconds = compute_retry_backoff(attempt, adk_stream_retry_backoff_s)
            if report:
                report.record_stream_retry_backoff(attempt, backoff_seconds)
            await asyncio.sleep(backoff_seconds)


# ---------------------------------------------------------------------------
# Gemini → Browser
# ---------------------------------------------------------------------------

async def _forward_to_client(
    websocket: WebSocket,
    adk_runner: Runner,
    live_queue: LiveRequestQueue,
    session_id: str = "",
    runtime_state: dict | None = None,
    wb_queue: asyncio.Queue | None = None,
    topic_queue: asyncio.Queue | None = None,
    *,
    firestore_client: Any | None,
    latency_state: dict[str, dict],
    debug_counters: dict[str, dict],
    debug_logger: logging.Logger,
    build_session_run_config: BuildRunConfigFn,
    adk_stream_max_retries: int,
    adk_stream_retry_backoff_s: float,
    memory_checkpoint_interval_s: int,
    live_compression_trigger_tokens: int,
    live_compression_target_tokens: int,
    response_ref_max_age_ms: int,
    turn_to_turn_max_gap_ms: int,
    report: Any | None = None,
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
            build_session_run_config=build_session_run_config,
            adk_stream_max_retries=adk_stream_max_retries,
            adk_stream_retry_backoff_s=adk_stream_retry_backoff_s,
            runtime_state=runtime_state,
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
                        await _persist_memory_checkpoint(
                            runtime_state,
                            session_id,
                            reason="topic_switch",
                            firestore_client=firestore_client,
                            checkpoint_interval_s=memory_checkpoint_interval_s,
                            send_json=_send_json,
                            websocket=websocket,
                            report=report,
                            force=True,
                        )
                    except asyncio.QueueEmpty:
                        break

            dc = debug_counters.get(session_id)
            if dc is not None:
                dc["last_gemini_event_at"] = time.time()

            token_estimate = extract_total_token_estimate(event)
            if token_estimate is not None and token_estimate > 0:
                runtime_state["live_token_estimate"] = int(token_estimate)
                trigger_tokens = int(runtime_state.get("live_compression_trigger_tokens", live_compression_trigger_tokens))
                last_notified = float(runtime_state.get("live_compression_last_notified_at", 0.0))
                now_ts = time.time()
                if token_estimate >= trigger_tokens and (now_ts - last_notified) >= 20.0:
                    runtime_state["live_compression_last_notified_at"] = now_ts
                    await _send_json(
                        websocket,
                        {
                            "type": "context_compression",
                            "data": {
                                "token_estimate": int(token_estimate),
                                "trigger_tokens": trigger_tokens,
                                "target_tokens": int(runtime_state.get("live_compression_target_tokens", live_compression_target_tokens)),
                            },
                        },
                    )
                    await _send_json(
                        websocket,
                        {
                            "type": "assistant_state",
                            "data": {
                                "state": "compressing_context",
                                "reason": "token_threshold",
                                "token_estimate": int(token_estimate),
                            },
                        },
                    )
                    if report:
                        report.record_context_compression(
                            int(token_estimate),
                            trigger_tokens,
                            target_tokens=int(
                                runtime_state.get("live_compression_target_tokens", live_compression_target_tokens)
                            ),
                        )
                    await _persist_memory_checkpoint(
                        runtime_state,
                        session_id,
                        reason="compression_threshold",
                        firestore_client=firestore_client,
                        checkpoint_interval_s=memory_checkpoint_interval_s,
                        send_json=_send_json,
                        websocket=websocket,
                        report=report,
                        force=False,
                    )

            # If a turn was gated out due missing student/proactive trigger, suppress
            # its output until the matching turn_complete boundary.
            suppress_output = drop_turn_in_progress
            if drop_turn_in_progress and event.turn_complete:
                drop_turn_in_progress = False
                runtime_state["assistant_speaking"] = False
                audio_response_chunks = 0
                turn_had_output = False
                debug_logger.debug("TURN_DROPPED_COMPLETE sid=%s (no ticket)", session_id[:8])

            if not suppress_output and event.content and event.content.parts:
                # Greeting gate: after first greeting turn, suppress output until student speaks.
                # Prevents Gemini from producing duplicate greetings from [SESSION START].
                if runtime_state.get("_greeting_delivered") and not runtime_state.get("_student_has_spoken") and not turn_had_output:
                    suppress_output = True

                # General turn gate: allow only one tutor turn per student/proactive trigger.
                elif int(runtime_state.get("_turn_ticket_count", 0)) <= 0:
                    suppress_output = True
                    runtime_state["assistant_speaking"] = False
                    if not event.turn_complete:
                        drop_turn_in_progress = True
                        debug_logger.debug("TURN_DROPPED_START sid=%s (no ticket)", session_id[:8])
                    else:
                        debug_logger.debug("TURN_DROPPED_ONE_EVENT sid=%s (no ticket)", session_id[:8])
                    logger.info("Suppressed extra tutor turn without new student activity")

            # --- Audio and text from content parts ---
            if event.content and event.content.parts and not suppress_output:
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
                            debug_logger.debug(
                                "SPEAKING_START sid=%s (was silent, now audio)",
                                session_id[:8],
                            )
                            lat = latency_state.get(session_id)
                            if lat and lat.get("awaiting_first_response") and lat.get("last_audio_in", 0) > 0:
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
                            # Close greeting gate on first audio reaching the
                            # browser — echo-triggered interrupts can no longer
                            # reset tickets and cause duplicate greetings.
                            if not runtime_state.get("_greeting_delivered"):
                                runtime_state["_greeting_delivered"] = True
                                debug_logger.debug(
                                    "GREETING_GATE_CLOSED sid=%s (first audio out)",
                                    session_id[:8],
                                )
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
                        _append_tutor_turn_part(runtime_state, cleaned, source="text")
                        logger.info("TUTOR TEXT: %s", cleaned)
                        runtime_state["last_user_activity_at"] = time.time()
                        runtime_state["idle_stage"] = 0
                        runtime_state["assistant_speaking"] = True
                        runtime_state["conversation_started"] = True
                        runtime_state["mic_kickoff_sent"] = True
                        reset_silence_tracking(runtime_state)
                        if dc is not None:
                            dc["text_out"] += 1
                        debug_logger.debug(
                            "TEXT sid=%s data=%s",
                            session_id[:8], str(cleaned)[:120],
                        )
                        await _send_json(websocket, {"type": "text", "data": cleaned})
                        turn_had_output = True

            # --- Turn complete ---
            if event.turn_complete:
                if suppress_output:
                    # If this turn was suppressed, skip normal turn_complete logic.
                    # Drop logic has already handled state cleanup.
                    # We just need to log the greeting gate drop if that was the reason.
                    if runtime_state.get("_greeting_delivered") and not runtime_state.get("_student_has_spoken") and not drop_turn_in_progress and int(runtime_state.get("_turn_ticket_count", 0)) > 0:
                        runtime_state["assistant_speaking"] = False
                        debug_logger.debug("TURN_COMPLETE_SUPPRESSED sid=%s (greeting gate)", session_id[:8])
                        logger.info("Suppressed duplicate greeting turn (turn #%d)", turn_count + 1)
                        audio_response_chunks = 0
                    continue

                # Ignore no-output synthetic turn_complete bursts when no ticket is available.
                if int(runtime_state.get("_turn_ticket_count", 0)) <= 0 and not turn_had_output:
                    debug_logger.debug("TURN_COMPLETE_SUPPRESSED sid=%s (no ticket/no output)", session_id[:8])
                    continue

                turn_count += 1
                runtime_state["assistant_speaking"] = False
                runtime_state["last_user_activity_at"] = time.time()
                runtime_state["idle_stage"] = 0
                reset_silence_tracking(runtime_state)
                if dc is not None:
                    dc["turn_complete"] += 1
                debug_logger.debug("TURN_COMPLETE sid=%s", session_id[:8])
                await _send_json(websocket, {"type": "turn_complete"})
                logger.info(
                    "Turn #%d complete — sent %d audio chunks to browser",
                    turn_count, audio_response_chunks,
                )
                if report:
                    report.record_turn_complete(audio_response_chunks)
                audio_response_chunks = 0
                completed_with_output = turn_had_output
                if turn_had_output and int(runtime_state.get("_turn_ticket_count", 0)) > 0:
                    runtime_state["_turn_ticket_count"] = int(runtime_state.get("_turn_ticket_count", 0)) - 1
                turn_had_output = False
                turn_snapshot = _finalize_tutor_turn(runtime_state)
                await _persist_memory_checkpoint(
                    runtime_state,
                    session_id,
                    reason="interval_turn",
                    firestore_client=firestore_client,
                    checkpoint_interval_s=memory_checkpoint_interval_s,
                    send_json=_send_json,
                    websocket=websocket,
                    report=report,
                    force=False,
                )

                # Track whether the tutor is waiting on a student reply and detect
                # near-duplicate prompt loops when no new student activity happened.
                turn_text = str(turn_snapshot.get("turn_text") or "").strip()
                if completed_with_output and turn_text:
                    await _maybe_store_question_answer_note(
                        session_id,
                        runtime_state,
                        turn_text,
                        wb_queue,
                        firestore_client,
                        report,
                    )
                    await _maybe_store_example_note(
                        session_id,
                        runtime_state,
                        turn_text,
                        wb_queue,
                        firestore_client,
                        report,
                    )
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

                    # Track question streak using the SAME metric as the
                    # scorecard (text.endswith("?")) — not the lenient
                    # is_question_like_turn() heuristic.
                    if turn_text.rstrip().endswith("?"):
                        runtime_state["question_like_streak"] = int(
                            runtime_state.get("question_like_streak", 0)
                        ) + 1
                    else:
                        runtime_state["question_like_streak"] = 0

                    # Fire after just 1 consecutive question so the model
                    # gets the nudge BEFORE producing a 2nd question turn.
                    # The live audio pipeline is real-time — waiting until
                    # streak==2 means turn 3 is already being generated.
                    q_streak = int(runtime_state.get("question_like_streak", 0))
                    if q_streak >= 1:
                        now = time.time()
                        if (now - float(runtime_state.get("last_hidden_prompt_at", 0.0))) >= 2.0:
                            # Escalate: use stronger prompt after 3+ streak
                            if q_streak >= 3:
                                ctrl_prompt = ANTI_QUESTION_LOOP_ESCALATED_PROMPT
                            else:
                                ctrl_prompt = ANTI_QUESTION_LOOP_CONTROL_PROMPT
                            logger.info(
                                "Session %s: ANTI-QUESTION control fired (streak=%d, escalated=%s)",
                                session_id, q_streak, q_streak >= 3,
                            )
                            live_queue.send_content(
                                types.Content(
                                    role="user",
                                    parts=[types.Part(text=ctrl_prompt)],
                                )
                            )
                            runtime_state["last_hidden_prompt_at"] = now
                    runtime_state["last_tutor_prompt_text"] = turn_text
                    runtime_state["last_tutor_prompt_activity_count"] = activity_count
                else:
                    runtime_state["awaiting_student_reply"] = False

                # Fallback: also set greeting gate at turn_complete if not
                # already closed by the first-audio-out path (e.g. text-only turns).
                if not runtime_state.get("_greeting_delivered") and completed_with_output:
                    runtime_state["_greeting_delivered"] = True

            # --- Interrupted ---
            if event.interrupted:
                # Stale interrupt filter: if assistant already stopped speaking
                # and no audio chunks were sent this turn, skip forwarding to client.
                if not runtime_state.get("assistant_speaking") and audio_response_chunks == 0:
                    debug_logger.debug(
                        "INTERRUPTED_STALE sid=%s (already silent, 0 chunks)", session_id[:8],
                    )
                    if report:
                        report.record_stale_interruption()
                    continue
                runtime_state["assistant_speaking"] = False
                # During the greeting phase, interrupts are typically echo
                # from the tutor's own audio — not real student speech.
                # Only unlock turn tickets when the student has actually
                # spoken (confirmed by barge_in or input_transcription).
                if runtime_state.get("_student_has_spoken"):
                    _mark_student_activity(runtime_state, unlock_turn=True)
                else:
                    # Minimal idle reset — no ticket unlock.
                    runtime_state["last_user_activity_at"] = time.time()
                    runtime_state["idle_stage"] = 0
                    reset_silence_tracking(runtime_state)
                if dc is not None:
                    dc["interrupted"] += 1
                debug_logger.debug("INTERRUPTED sid=%s student_spoken=%s", session_id[:8], runtime_state.get("_student_has_spoken"))
                if report:
                    report.record_interruption()
                lat = latency_state.get(session_id)
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

            # --- Transcription ---
            if event.input_transcription and event.input_transcription.text and event.input_transcription.finished:
                # Echo guard: while tutor output is active, transcription often captures
                # assistant audio from speakers. In that case, ignore the transcript content.
                now = time.time()
                is_echo_window = (
                    runtime_state.get("assistant_speaking")
                    or (now - float(runtime_state.get("_last_tutor_audio_at", 0.0))) < 0.8
                )
                if is_echo_window:
                    debug_logger.debug("INPUT_TRANSCRIPT_IGNORED sid=%s (echo-window)", session_id[:8])
                    continue
                student_text = event.input_transcription.text
                logger.info("STUDENT TRANSCRIPT: %s", student_text)

                # Only confirmed non-echo transcripts unlock new tutor turns.
                _mark_student_activity(runtime_state, unlock_turn=True)
                runtime_state["_student_has_spoken"] = True
                # Forced search injection removed — google_search tool + system prompt
                # instruction ("call google_search before answering if unsure") handles this
                # without burning an extra hidden turn per search request.

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
                # Guardrail reinforcement: log only, no hidden turn injection
                # (system prompt already enforces guardrails — extra turns burn tokens)
                if student_guardrail_events:
                    reason = student_guardrail_events[0]["guardrail"] if student_guardrail_events else "unknown"
                    record_reinforcement(runtime_state, reason)
                    logger.info("Guardrail student event logged (no injection): %s", reason)

                # Capture the latest on-topic study question for "My notes".
                if student_guardrail_events:
                    runtime_state["pending_study_question"] = None
                else:
                    topic_title = str(runtime_state.get("topic_title") or "")
                    if is_study_related_question(student_text, topic_title):
                        runtime_state["pending_study_question"] = {
                            "text": student_text,
                            "asked_at": now,
                            "topic_title": topic_title,
                        }
                        logger.info(
                            "Session %s: queued study question for auto-note",
                            session_id,
                        )
                    elif is_student_question(student_text):
                        runtime_state["pending_study_question"] = None

                append_transcript_piece(
                    runtime_state,
                    role="student",
                    text=student_text,
                    at=now,
                )
                if report:
                    report.record_student_transcript(student_text)

            if event.output_transcription and event.output_transcription.text and event.output_transcription.finished:
                # Ignore model output transcripts for turns that were gated out.
                # Without this, internal/suppressed turns can still be recorded
                # as tutor speech even when no audio/text reached the client.
                suppress_tutor_transcript = (
                    drop_turn_in_progress
                    or (
                        runtime_state.get("_greeting_delivered")
                        and not runtime_state.get("_student_has_spoken")
                        and not turn_had_output
                    )
                    or (
                        int(runtime_state.get("_turn_ticket_count", 0)) <= 0
                        and not turn_had_output
                    )
                )
                if suppress_tutor_transcript:
                    debug_logger.debug(
                        "OUTPUT_TRANSCRIPT_SUPPRESSED sid=%s (gated turn)",
                        session_id[:8],
                    )
                    continue

                tutor_text_raw = event.output_transcription.text
                tutor_text, had_internal = sanitize_tutor_output(tutor_text_raw)
                if had_internal:
                    logger.info("Sanitized leaked internal text from tutor transcript")
                if not tutor_text:
                    continue

                logger.info("TUTOR TRANSCRIPT: %s", tutor_text)
                _append_tutor_turn_part(runtime_state, tutor_text, source="transcript")

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
                # Guardrail tutor reinforcement: log only, no hidden turn injection
                # (system prompt already enforces Socratic method — extra turns burn tokens)
                if tutor_guardrail_events:
                    record_reinforcement(runtime_state, "answer_leak")
                    logger.info("Guardrail tutor event logged (no injection): answer_leak")

                if report:
                    report.record_tutor_transcript(tutor_text)
                append_transcript_piece(
                    runtime_state,
                    role="tutor",
                    text=tutor_text,
                )

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
