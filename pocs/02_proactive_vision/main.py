"""
POC 02 — Proactive Vision

Minimal FastAPI + WebSocket backend that connects to the Gemini Live API
and tests the tutor's ability to proactively comment on visual content
without being asked, using a goal-driven mission-control flow.

Three concurrent tasks per session:
  1. Browser → Gemini: forwards audio + video frames
  2. Gemini → Browser: forwards audio/text responses + detects proactive triggers
  3. Idle Orchestrator: monitors silence + camera, escalates poke → nudge

Key behaviors tested:
  - Proactive trigger: tutor speaks up during student silence with visible work
  - Progressive disclosure: one issue at a time
  - Goal-driven flow: Goal → Grounding → Plan → Execute → Closeout
  - Backend idle escalation: soft poke first, hard nudge fallback

Usage:
    cd pocs/02_proactive_vision
    uvicorn main:app --reload --port 8200
    # Open http://localhost:8200
"""

import asyncio
import base64
import binascii
import datetime
import json
import logging
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("poc_proactive_vision")

# ---------------------------------------------------------------------------
# Gemini backend: Vertex AI (same auth as main app)
# ---------------------------------------------------------------------------
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT_ID", "seeme-tutor"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_REGION", "europe-west1"))

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Native audio model sounds more natural; switch to 2.0 if video input fails
MODEL = "gemini-live-2.5-flash-native-audio"
# MODEL = "gemini-2.0-flash-live-preview-04-09"  # Fallback: known to work with video

# Idle orchestrator thresholds
ORGANIC_POKE_THRESHOLD_S = 6.0    # Faster soft poke for quicker visual recognition
HARD_NUDGE_THRESHOLD_S = 9.0      # Faster fallback if no response after poke
CHECK_INTERVAL_S = 0.2            # How often the idle loop checks
CAMERA_ACTIVE_TIMEOUT_S = 3.0     # Camera considered off if no frame within this window
POKE_RESPONSE_GRACE_S = 1.2       # Wait after soft poke before escalating to hard nudge

# Proactive trigger detection
PROACTIVE_SILENCE_MIN_S = 5.0      # Min silence to count tutor speech as "proactive"
NUDGE_ATTRIBUTION_WINDOW_S = 5.0   # If nudge was sent within this window, attribute trigger to it
RESUME_CONTEXT_MAX_CHARS = 6000
STUDENT_SPEECH_STALE_TIMEOUT_S = 8.0
STALE_RESET_GRACE_S = 3.0
VISUAL_CHANGE_SCORE_THRESHOLD = 0.19
VISUAL_CHANGE_MIN_SILENCE_S = 1.0
VISUAL_CHANGE_PROMPT_COOLDOWN_S = 8.0
VISUAL_CHANGE_AFTER_TUTOR_OUTPUT_COOLDOWN_S = 5.0
HIDDEN_PROMPT_MIN_GAP_S = 4.0

# ---------------------------------------------------------------------------
# System Prompt — Goal-driven proactive visual tutor
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are SeeMe, an observant visual tutor. You can see the student's work through their camera.

═══ YOUR MOST IMPORTANT BEHAVIOR ═══
You PROACTIVELY comment on what you see. You do NOT wait to be asked.
When the student is silent and you can see their work, you SPEAK UP with a helpful observation.
This is what makes you different from a chatbot — you are an active observer.

═══ SESSION FLOW (follow in order) ═══

1. GOAL CONTRACT (first thing you do)
   - Ask: "What are we working on today?"
   - If you can already see work on camera, propose: "I can see [description] — shall we work through that?"
   - Confirm done-criteria: "We'll be done when [specific outcome]."

2. GROUNDING (before ANY visual claim)
   - Say what you see: "I can see you wrote..."
   - If unsure: "I think that says [X] — is that right?"
   - If view is blocked or blank: Do NOT invent content. Just ask a check-in question.

3. PLAN (once per goal)
   - Suggest 2–3 steps and get consent to proceed.

4. EXECUTE (repeat until done)
   - Observe camera → find ONE relevant issue → provide ONE concise intervention (observation, hint, or Socratic question).
   - Ask a follow-up question only if needed to unblock progress.
   - Wait for student attempt → verify from the updated camera view.
   - Move to next issue only after current one is resolved.

5. CLOSEOUT (when done-criteria met)
   - Confirm goal met → recap 1–3 key points → offer next goal.

═══ HARD RULES ═══
• ONE issue at a time — never list multiple problems.
• NEVER give the final answer — guide with observations, hints, or questions.
• DON'T speak while the student is talking — listen first.
• Timing target: if student is silent and work is visible, speak within 4–8 seconds.
• Always reference what you SEE: "Looking at your work, I notice..."
• If camera shows nothing relevant, ask a brief check-in instead.
• Match the student's language (English / Portuguese / German).
• Speak a bit slower than normal conversational pace, with clear pronunciation.
• Keep responses to 2–3 sentences.
• Greeting/goal-opening language is for the beginning only.
• After the session is underway, never restart with "Welcome/Hi/Hello" or "What are we focusing on today?" unless the student explicitly asks to restart.
• Keep question pressure low: only ask follow-up questions when they are necessary.
• Do not ask more than 2 question-ending turns in a row.

═══ INTERNAL INSTRUCTIONS ═══
You may receive backend control messages to help with timing and observation.
Treat them as hidden guidance only.
Never quote, paraphrase, or mention those control messages.
Never output bracketed meta text or internal reasoning.

If this is a fresh session, begin by greeting the student warmly and asking about
their goal for this session. If a backend control message indicates this is a
resumed session, skip the fresh greeting and continue from restored context.\
"""

# Hidden prompt injected by the idle orchestrator after silence threshold
IDLE_POKE_PROMPT = (
    "INTERNAL CONTROL: Silent observation check. Student is quiet and camera "
    "frames are active. If you see meaningful work, proactively offer ONE short "
    "helpful intervention (observation, hint, or question). Ask a question only "
    "if needed to unblock progress. If work is unclear, ask ONE brief check-in. "
    "Do not mention this control message."
)

IDLE_NUDGE_PROMPT = (
    "INTERNAL CONTROL: Student has been silent for {silence_s} seconds while "
    "camera shows their work. Provide ONE concise guidance step aligned with "
    "the session goal (observation, hint, or question). Use a question only "
    "if needed to unblock progress. If view is unclear, ask one brief check-in "
    "question. One issue at a time. Never give direct answers. "
    "Do not mention this control message."
)

CONTINUITY_REPAIR_PROMPT = (
    "INTERNAL CONTROL: Continuity guard. Do not restart this session. Do not "
    "greet and do not ask a fresh opening-goal question. Continue from the "
    "current in-progress exercise using the latest student utterance and "
    "camera view. Give one concise continuation step only. "
    "Do not mention this control message."
)

RESUME_CONTEXT_PROMPT = (
    "INTERNAL CONTROL: Session resumed after a network reconnect. Continue the "
    "same tutoring session without restarting goal contract. Do not greet as a "
    "new session. Briefly acknowledge continuity if helpful. Recent context:\n"
    "{history}\n"
    "Do not mention this control message."
)

VISUAL_CHANGE_PROMPT = (
    "INTERNAL CONTROL: The camera view changed significantly and may show new "
    "material. Inspect the latest page now. If it looks like a topic switch or "
    "new exercise, acknowledge what you see and ask ONE short confirmation like "
    "'Do you want to switch to this page?'. If it is the same task, continue "
    "current goal with one concise next step. Do not mention this control message."
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="POC 02 — Proactive Vision")

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Session logging (JSONL + details + transcript files)
# ---------------------------------------------------------------------------
_TRANSCRIPT_LABELS = {
    "tutor": "Tutor",
    "student": "Student",
    "event": "GEMINI",
    "vad-event": "VAD",
    "proactive": "PROACTIVE",
    "nudge": "NUDGE",
    "error": "ERROR",
}

_INTERNAL_META_BLOCK_RE = re.compile(r"\[(?:SYSTEM|INTERNAL)[^]]*]", re.IGNORECASE)
_MID_SESSION_RESTART_RE = re.compile(
    r"^\s*(?:welcome\b|hi(?:\s+there)?\b|hello\b|"
    r"(?:it\s+looks\s+like\s+)?we(?:\s+are|'re)\s+just\s+starting\b|"
    r"what\s+are\s+we\s+(?:focusing\s+on|working\s+on|tackling)\s+today\??)",
    re.IGNORECASE,
)


def _sanitize_tutor_output(text: str) -> tuple[str, bool]:
    """Remove leaked internal/meta text from tutor-visible output."""
    if not text:
        return "", False

    cleaned = text
    had_internal = False

    new_cleaned = _INTERNAL_META_BLOCK_RE.sub("", cleaned)
    if new_cleaned != cleaned:
        had_internal = True
        cleaned = new_cleaned

    # Safety fallback for accidental raw prefixes.
    upper_stripped = cleaned.lstrip().upper()
    if upper_stripped.startswith("SYSTEM:") or upper_stripped.startswith("INTERNAL CONTROL:"):
        had_internal = True
        return "", True

    if not cleaned.strip():
        return "", had_internal
    return cleaned, had_internal


def _is_mid_session_restart_text(text: str, turn_completes: int) -> bool:
    """Detect accidental session-restart utterances after the session is underway."""
    if turn_completes < 6:
        return False
    if not text:
        return False
    return bool(_MID_SESSION_RESTART_RE.search(text.strip()))


def _create_session_log(session_id: str):
    """Create per-session log files.

    Writes three files:
      - {ts}_{session_id}.jsonl  — raw JSONL (all events with state snapshots)
      - details.log              — human-readable event log, newest-first
      - transcript.log           — conversation transcript, newest-first
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"{ts}_{session_id}.jsonl"
    fh = open(path, "a", buffering=1)  # line-buffered

    details_lines: list[str] = []
    transcript_lines: list[str] = []

    def write(source: str, event: str, **extra):
        now = datetime.datetime.now()
        entry = {
            "ts": now.isoformat(timespec="milliseconds"),
            "t": round(time.time() * 1000),
            "src": source,
            "event": event,
            **extra,
        }
        fh.write(json.dumps(entry) + "\n")

        text = extra.get("text", "")
        if source != "client" or not text:
            return

        if event.startswith("transcript_"):
            tr_type = event[len("transcript_"):]
            label = _TRANSCRIPT_LABELS.get(tr_type, tr_type.upper())
            ts_short = now.strftime("%H:%M:%S")
            transcript_lines.append(f"{ts_short} {label}: {text}")
        else:
            ms = f"{now.microsecond // 1000:03d}"
            ts_detail = now.strftime("%H:%M:%S.") + ms
            details_lines.append(f"[{ts_detail}] {text}")

    def close_logs():
        fh.close()
        (LOGS_DIR / "details.log").write_text(
            "\n".join(reversed(details_lines)) + ("\n" if details_lines else "")
        )
        (LOGS_DIR / "transcript.log").write_text(
            "\n".join(reversed(transcript_lines)) + ("\n" if transcript_lines else "")
        )

    logger.info("Session log: %s", path)
    return fh, write, close_logs


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTMLResponse((BASE_DIR / "index.html").read_text())


@app.get("/health")
async def health():
    return {"status": "ok", "poc": "02_proactive_vision"}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"poc-{int(time.time())}"
    logger.info("Session %s: WebSocket connected", session_id)

    # ── Metrics ──
    metrics = {
        # Proactive vision
        "proactive_triggers": 0,
        "organic_triggers": 0,
        "nudge_triggers": 0,
        "backend_pokes": 0,
        "backend_nudges": 0,
        "silence_durations_s": [],
        "false_positives": 0,
        "internal_text_filtered": 0,
        "mid_session_restart_blocks": 0,
        "resume_context_applied": 0,
        "visual_change_events": 0,
        "visual_change_prompts": 0,
        # State tracking
        "tutor_speaking": False,
        "client_tutor_playing": False,
        "speaking_started_at": 0.0,
        "student_speaking": False,
        "last_student_speech_at": 0.0,
        "last_student_stale_reset_at": 0.0,
        "last_video_frame_at": 0.0,
        "silence_started_at": 0.0,
        "idle_poke_sent": False,
        "idle_nudge_sent": False,
        "last_poke_at": 0.0,
        "last_nudge_at": 0.0,
        "last_visual_change_prompt_at": 0.0,
        "last_hidden_prompt_at": 0.0,
        "last_tutor_output_at": 0.0,
        "has_seen_tutor_turn_complete": False,
        # General counters
        "turn_completes": 0,
        "audio_chunks_in": 0,
        "audio_chunks_out": 0,
        "video_frames_in": 0,
        "last_audio_out_at": 0.0,
    }

    log_fh, slog, close_logs = _create_session_log(session_id)

    try:
        client = genai.Client()
        slog("server", "session_start")

        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck",
                    ),
                ),
            ),
            system_instruction=types.Content(
                parts=[types.Part(text=SYSTEM_PROMPT)],
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    # LOW sensitivity: client-side VAD gates noise
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                    prefix_padding_ms=300,
                    silence_duration_ms=700,
                ),
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        async with client.aio.live.connect(model=MODEL, config=config) as session:
            forward_task = asyncio.create_task(
                _forward_browser_to_gemini(websocket, session, session_id, metrics, slog),
                name="browser_to_gemini",
            )
            receive_task = asyncio.create_task(
                _forward_gemini_to_browser(websocket, session, session_id, metrics, slog),
                name="gemini_to_browser",
            )
            idle_task = asyncio.create_task(
                _idle_orchestrator(websocket, session, session_id, metrics, slog),
                name="idle_orchestrator",
            )

            done, pending = await asyncio.wait(
                {forward_task, receive_task, idle_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except Exception as exc:
        logger.exception("Session %s: error: %s", session_id, exc)
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "data": str(exc),
            }))
        except Exception:
            pass

    finally:
        _log_final_metrics(session_id, metrics)
        slog("server", "session_end",
             proactive_triggers=metrics["proactive_triggers"],
             organic=metrics["organic_triggers"],
             nudge=metrics["nudge_triggers"],
             backend_pokes=metrics["backend_pokes"],
             backend_nudges=metrics["backend_nudges"],
             internal_text_filtered=metrics["internal_text_filtered"],
             mid_session_restart_blocks=metrics["mid_session_restart_blocks"],
             resume_context_applied=metrics["resume_context_applied"],
             visual_change_events=metrics["visual_change_events"],
             visual_change_prompts=metrics["visual_change_prompts"],
             false_positives=metrics["false_positives"],
             turns=metrics["turn_completes"],
             video_frames=metrics["video_frames_in"])
        close_logs()


# ---------------------------------------------------------------------------
# Browser → Gemini: audio + video + speech state
# ---------------------------------------------------------------------------
async def _forward_browser_to_gemini(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Receive audio, video frames, and control messages from the browser."""
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = message.get("type")

            # ── Audio ──
            if msg_type == "audio":
                encoded = message.get("data")
                if not encoded:
                    continue
                try:
                    audio_bytes = base64.b64decode(encoded)
                except binascii.Error:
                    continue

                metrics["audio_chunks_in"] += 1
                await session.send_realtime_input(
                    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                )

            # ── Video frame ──
            elif msg_type == "video":
                encoded = message.get("data")
                if not encoded:
                    continue
                try:
                    jpeg_bytes = base64.b64decode(encoded)
                except binascii.Error:
                    continue

                metrics["video_frames_in"] += 1
                metrics["last_video_frame_at"] = time.time()
                await session.send_realtime_input(
                    video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                )

            # ── VAD speech state from browser ──
            elif msg_type == "speech_start":
                now = time.time()
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                # Reset idle orchestrator state
                metrics["silence_started_at"] = 0.0
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                slog("client", "speech_start")

            elif msg_type == "speech_keepalive":
                now = time.time()
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                metrics["silence_started_at"] = 0.0
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False

            elif msg_type == "speech_end":
                now = time.time()
                metrics["student_speaking"] = False
                metrics["last_student_speech_at"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                # Start silence window
                metrics["silence_started_at"] = now
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                slog("client", "speech_end")

            # ── Barge-in (basic interruption support) ──
            elif msg_type == "barge_in":
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = time.time()
                metrics["silence_started_at"] = 0.0
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                slog("client", "vad_bargein",
                     client_latency_ms=message.get("client_latency_ms", 0))

            # ── Tutor playback state from browser (audio drain-aware) ──
            elif msg_type == "tutor_playback_start":
                now = time.time()
                metrics["client_tutor_playing"] = True
                metrics["tutor_speaking"] = True
                metrics["speaking_started_at"] = now
                metrics["silence_started_at"] = 0.0
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                slog("client", "tutor_playback_start")

            elif msg_type == "tutor_playback_end":
                now = time.time()
                metrics["client_tutor_playing"] = False
                metrics["tutor_speaking"] = False
                metrics["speaking_started_at"] = 0.0
                if not metrics["student_speaking"]:
                    metrics["silence_started_at"] = now
                    metrics["idle_poke_sent"] = False
                    metrics["idle_nudge_sent"] = False
                slog("client", "tutor_playback_end")

            # ── Client-side event logging ──
            elif msg_type == "client_log":
                slog("client", message.get("event", "log"),
                     text=message.get("text", ""),
                     **{k: v for k, v in message.items()
                        if k not in ("type", "event", "text")})

            # ── Visual novelty signal from browser ──
            elif msg_type == "visual_change":
                now = time.time()
                try:
                    score = float(message.get("score", 0.0))
                except (TypeError, ValueError):
                    score = 0.0

                metrics["visual_change_events"] += 1
                slog("server", "visual_change_detected",
                     score=round(score, 3),
                     count=metrics["visual_change_events"])

                if score < VISUAL_CHANGE_SCORE_THRESHOLD:
                    continue
                if metrics["student_speaking"] or metrics["tutor_speaking"] or metrics["client_tutor_playing"]:
                    continue
                if (
                    metrics["last_visual_change_prompt_at"] > 0
                    and (now - metrics["last_visual_change_prompt_at"]) < VISUAL_CHANGE_PROMPT_COOLDOWN_S
                ):
                    continue
                if (
                    metrics["last_hidden_prompt_at"] > 0
                    and (now - metrics["last_hidden_prompt_at"]) < HIDDEN_PROMPT_MIN_GAP_S
                ):
                    continue
                if (
                    metrics["last_tutor_output_at"] > 0
                    and (now - metrics["last_tutor_output_at"]) < VISUAL_CHANGE_AFTER_TUTOR_OUTPUT_COOLDOWN_S
                ):
                    continue

                camera_active = (
                    metrics["last_video_frame_at"] > 0
                    and (now - metrics["last_video_frame_at"]) < CAMERA_ACTIVE_TIMEOUT_S
                )
                if not camera_active:
                    continue

                silence_s = (
                    (now - metrics["silence_started_at"])
                    if metrics["silence_started_at"] > 0
                    else 0.0
                )
                if silence_s < VISUAL_CHANGE_MIN_SILENCE_S:
                    continue

                try:
                    await _send_hidden_turn(session, VISUAL_CHANGE_PROMPT)
                except Exception as exc:
                    logger.warning("Visual change prompt send failed: %s", exc)
                    slog("server", "visual_change_prompt_failed", error=str(exc))
                    continue

                metrics["visual_change_prompts"] += 1
                metrics["last_visual_change_prompt_at"] = now
                metrics["last_hidden_prompt_at"] = now
                slog("server", "visual_change_prompt",
                     score=round(score, 3),
                     silence_s=round(silence_s, 1),
                     count=metrics["visual_change_prompts"])
                try:
                    await websocket.send_text(json.dumps({
                        "type": "visual_change_prompt",
                        "data": {
                            "score": round(score, 3),
                            "count": metrics["visual_change_prompts"],
                        },
                    }))
                except Exception:
                    pass

            # ── Resume context after reconnect ──
            elif msg_type == "resume_context":
                history = message.get("history", "")
                if isinstance(history, list):
                    history = "\n".join(str(item) for item in history)
                if not isinstance(history, str):
                    continue

                history = history.strip()
                if not history:
                    continue

                clipped_history = history[:RESUME_CONTEXT_MAX_CHARS]
                resume_prompt = RESUME_CONTEXT_PROMPT.format(history=clipped_history)
                try:
                    await _send_hidden_turn(session, resume_prompt)
                except Exception as exc:
                    logger.warning("Resume context send failed: %s", exc)
                    slog("server", "resume_context_failed", error=str(exc))
                    continue

                metrics["resume_context_applied"] += 1
                slog("server", "resume_context_applied",
                     chars=len(clipped_history),
                     count=metrics["resume_context_applied"])
                try:
                    await websocket.send_text(json.dumps({
                        "type": "resume_applied",
                        "data": {
                            "count": metrics["resume_context_applied"],
                        },
                    }))
                except Exception:
                    pass

            # ── Activity signals (for Gemini's VAD) ──
            elif msg_type == "activity_start":
                slog("client", "activity_start")
                await session.send_realtime_input(
                    activity_start=types.ActivityStart(),
                )
            elif msg_type == "activity_end":
                slog("client", "activity_end")
                await session.send_realtime_input(
                    activity_end=types.ActivityEnd(),
                )

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected (forward)", session_id)
    except Exception as exc:
        logger.exception("Session %s: forward error: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Gemini → Browser: audio, text, interruptions, transcriptions
# ---------------------------------------------------------------------------
async def _forward_gemini_to_browser(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Receive responses from Gemini and forward to the browser."""
    turn_index = 0
    turn_had_tutor_output = False
    restart_guard_triggered = False

    async def _trigger_restart_guard(blocked_text: str, source: str):
        nonlocal restart_guard_triggered
        if restart_guard_triggered:
            return

        restart_guard_triggered = True
        metrics["mid_session_restart_blocks"] += 1
        logger.warning(
            "Session %s: mid-session restart utterance blocked (%s): %s",
            session_id,
            source,
            blocked_text,
        )
        slog(
            "server",
            "mid_session_restart_blocked",
            source=source,
            text=blocked_text[:200],
            count=metrics["mid_session_restart_blocks"],
        )

        # Stop currently playing tutor audio on the client as quickly as possible.
        metrics["tutor_speaking"] = False
        metrics["speaking_started_at"] = 0.0
        try:
            await websocket.send_text(json.dumps({
                "type": "interrupted",
                "data": {"source": "continuity_guard"},
            }))
        except Exception:
            pass

        # Immediately steer the model back to continuity mode.
        try:
            await _send_hidden_turn(session, CONTINUITY_REPAIR_PROMPT)
            metrics["last_hidden_prompt_at"] = time.time()
        except Exception as exc:
            logger.warning("Continuity repair prompt failed: %s", exc)
            slog("server", "continuity_repair_failed", error=str(exc))

    try:
        while True:
            turn_index += 1
            turn_events = 0
            restart_guard_triggered = False

            async for msg in session.receive():
                turn_events += 1

                # Skip tool calls (not used in this POC)
                if getattr(msg, "tool_call", None) is not None:
                    continue

                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                # ── Interruption (Gemini server-side) ──
                if getattr(server_content, "interrupted", False):
                    now = time.time()
                    if not metrics["tutor_speaking"]:
                        slog("server", "gemini_interrupt_ignored",
                             reason="tutor_not_speaking")
                        continue

                    metrics["tutor_speaking"] = False
                    metrics["speaking_started_at"] = 0.0

                    logger.info("GEMINI INTERRUPTED")
                    slog("server", "gemini_interrupted")

                    await websocket.send_text(json.dumps({
                        "type": "interrupted",
                        "data": {"source": "gemini"},
                    }))
                    continue

                # ── Turn complete ──
                turn_complete = getattr(server_content, "turn_complete", False)

                # ── Audio / text content ──
                model_turn = getattr(server_content, "model_turn", None)
                if model_turn is not None:
                    parts = getattr(model_turn, "parts", None) or []
                    for part in parts:
                        # Audio output
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data is not None and inline_data.data:
                            now = time.time()
                            if restart_guard_triggered:
                                continue
                            turn_had_tutor_output = True

                            # Detect proactive trigger on first audio chunk
                            if not metrics["tutor_speaking"]:
                                metrics["tutor_speaking"] = True
                                metrics["speaking_started_at"] = now
                                await _check_proactive_trigger(
                                    now, metrics, slog, websocket
                                )

                            metrics["audio_chunks_out"] += 1
                            metrics["last_audio_out_at"] = now
                            metrics["last_tutor_output_at"] = now

                            encoded = base64.b64encode(inline_data.data).decode("utf-8")
                            await websocket.send_text(json.dumps({
                                "type": "audio",
                                "data": encoded,
                            }))

                        # Text output
                        text = getattr(part, "text", None)
                        if text:
                            safe_text, had_internal = _sanitize_tutor_output(text)
                            if had_internal:
                                metrics["internal_text_filtered"] += 1
                                slog("server", "internal_text_filtered", source="model_turn_text")
                            if safe_text and _is_mid_session_restart_text(
                                safe_text, metrics["turn_completes"]
                            ):
                                await _trigger_restart_guard(safe_text, "model_turn_text")
                                continue
                            if restart_guard_triggered:
                                continue
                            if safe_text:
                                turn_had_tutor_output = True
                                metrics["last_tutor_output_at"] = time.time()
                                logger.info("TUTOR: %s", safe_text)
                                slog("server", "tutor_text", text=safe_text)
                                await websocket.send_text(json.dumps({
                                    "type": "text",
                                    "data": safe_text,
                                }))

                # ── Input transcription (student speech) ──
                input_transcription = getattr(server_content, "input_transcription", None)
                if input_transcription is not None:
                    transcript_text = getattr(input_transcription, "text", None)
                    if transcript_text:
                        logger.info("STUDENT: %s", transcript_text)
                        slog("server", "student_transcript", text=transcript_text)
                        # Also update last speech time from server-side detection
                        metrics["last_student_speech_at"] = time.time()
                        await websocket.send_text(json.dumps({
                            "type": "input_transcript",
                            "data": transcript_text,
                        }))

                # ── Output transcription (tutor speech) ──
                output_transcription = getattr(server_content, "output_transcription", None)
                if output_transcription is not None:
                    transcript_text = getattr(output_transcription, "text", None)
                    if transcript_text:
                        safe_transcript, had_internal = _sanitize_tutor_output(transcript_text)
                        if had_internal:
                            metrics["internal_text_filtered"] += 1
                            slog("server", "internal_text_filtered", source="output_transcription")
                        if safe_transcript and _is_mid_session_restart_text(
                            safe_transcript, metrics["turn_completes"]
                        ):
                            await _trigger_restart_guard(safe_transcript, "output_transcription")
                            continue
                        if restart_guard_triggered:
                            continue
                        if safe_transcript:
                            turn_had_tutor_output = True
                            metrics["last_tutor_output_at"] = time.time()
                            await websocket.send_text(json.dumps({
                                "type": "output_transcript",
                                "data": safe_transcript,
                            }))

                # ── Turn complete ──
                if turn_complete:
                    metrics["turn_completes"] += 1
                    metrics["tutor_speaking"] = False
                    metrics["speaking_started_at"] = 0.0
                    metrics["has_seen_tutor_turn_complete"] = True
                    if restart_guard_triggered:
                        turn_had_tutor_output = False
                        slog(
                            "server",
                            "mid_session_restart_suppressed_turn",
                            count=metrics["turn_completes"],
                        )

                    # Start a fresh silence window only when tutor actually spoke.
                    # If a control turn completes with no tutor output, keep the
                    # current silence/escalation state so hard nudge can still fire.
                    if turn_had_tutor_output and not metrics["student_speaking"]:
                        metrics["silence_started_at"] = time.time()
                        metrics["idle_poke_sent"] = False
                        metrics["idle_nudge_sent"] = False
                    elif not turn_had_tutor_output:
                        slog("server", "turn_complete_no_tutor_output",
                             count=metrics["turn_completes"])

                    logger.info("TURN COMPLETE #%d", metrics["turn_completes"])
                    slog("server", "turn_complete", count=metrics["turn_completes"])
                    await websocket.send_text(json.dumps({
                        "type": "turn_complete",
                        "data": {"count": metrics["turn_completes"]},
                    }))
                    turn_had_tutor_output = False
                    restart_guard_triggered = False

            if turn_events == 0:
                logger.info("Session %s: Gemini stream ended", session_id)
                return
            await asyncio.sleep(0)

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected (receive)", session_id)
    except Exception as exc:
        logger.exception("Session %s: receive error: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Proactive trigger detection
# ---------------------------------------------------------------------------
async def _check_proactive_trigger(
    now: float,
    metrics: dict,
    slog,
    websocket: WebSocket,
):
    """Check if tutor is speaking proactively (during student silence).

    A "proactive trigger" means the tutor started speaking without the student
    having spoken recently. This is the core behavior we're testing.
    """
    # Ignore session bootstrap greeting/intro before first completed tutor turn.
    if not metrics["has_seen_tutor_turn_complete"]:
        return

    # Use current silence window when available to avoid overcounting from prior turns.
    silence_anchor = metrics["silence_started_at"] or metrics["last_student_speech_at"]
    if silence_anchor <= 0:
        return
    silence_s = now - silence_anchor

    if silence_s < PROACTIVE_SILENCE_MIN_S:
        return  # Student spoke recently — normal conversational response

    # Is camera active?
    camera_active = (
        metrics["last_video_frame_at"] > 0
        and (now - metrics["last_video_frame_at"]) < CAMERA_ACTIVE_TIMEOUT_S
    )

    metrics["proactive_triggers"] += 1
    metrics["silence_durations_s"].append(round(silence_s, 1))

    # Attribute to nudge or organic
    nudge_recent = (
        metrics["last_nudge_at"] > 0
        and (now - metrics["last_nudge_at"]) < NUDGE_ATTRIBUTION_WINDOW_S
    )
    if nudge_recent:
        metrics["nudge_triggers"] += 1
        trigger_type = "nudge"
    else:
        metrics["organic_triggers"] += 1
        trigger_type = "organic"

    # False positive: camera not active but tutor speaking proactively
    if not camera_active:
        metrics["false_positives"] += 1

    logger.info(
        "PROACTIVE TRIGGER #%d [%s] — silence=%.1fs, camera=%s",
        metrics["proactive_triggers"], trigger_type, silence_s,
        "ON" if camera_active else "OFF",
    )
    slog("server", "proactive_trigger",
         trigger_type=trigger_type,
         silence_s=round(silence_s, 1),
         camera_active=camera_active,
         count=metrics["proactive_triggers"])

    try:
        await websocket.send_text(json.dumps({
            "type": "proactive_trigger",
            "data": {
                "trigger_type": trigger_type,
                "silence_s": round(silence_s, 1),
                "camera_active": camera_active,
                "count": metrics["proactive_triggers"],
            },
        }))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Idle Orchestrator — monitors silence and injects nudge prompts
# ---------------------------------------------------------------------------
async def _send_hidden_turn(session, text: str):
    """Send hidden system guidance as a synthetic user turn."""
    await session.send_client_content(
        turns=types.Content(
            role="user",
            parts=[types.Part(text=text)],
        ),
        turn_complete=True,
    )


async def _idle_orchestrator(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Background task that escalates from soft poke to hard nudge.

    Stage 1 (soft poke): at ORGANIC_POKE_THRESHOLD_S, send a lightweight
    observation check so Gemini can proactively respond on its own.
    Stage 2 (hard nudge): at HARD_NUDGE_THRESHOLD_S, inject explicit guidance
    if Stage 1 did not produce tutor speech.
    """
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)

            now = time.time()

            # Don't nudge if tutor is currently speaking
            if metrics["tutor_speaking"]:
                continue
            if metrics["client_tutor_playing"]:
                continue

            # Don't nudge if student is currently speaking
            if metrics["student_speaking"]:
                stale_s = now - metrics["last_student_speech_at"]
                if stale_s > STUDENT_SPEECH_STALE_TIMEOUT_S:
                    metrics["student_speaking"] = False
                    metrics["last_student_stale_reset_at"] = now
                    metrics["silence_started_at"] = 0.0
                    metrics["idle_poke_sent"] = False
                    metrics["idle_nudge_sent"] = False
                    slog("server", "student_speaking_stale_reset",
                         stale_s=round(stale_s, 1))
                else:
                    metrics["silence_started_at"] = 0.0
                    metrics["idle_poke_sent"] = False
                    metrics["idle_nudge_sent"] = False
                    continue

            # Student may have just transitioned to silence due to stale reset.
            if metrics["student_speaking"]:
                metrics["silence_started_at"] = 0.0
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                continue
            if (
                metrics["last_student_stale_reset_at"] > 0
                and (now - metrics["last_student_stale_reset_at"]) < STALE_RESET_GRACE_S
            ):
                continue

            # Check if camera is active (received frame recently)
            camera_active = (
                metrics["last_video_frame_at"] > 0
                and (now - metrics["last_video_frame_at"]) < CAMERA_ACTIVE_TIMEOUT_S
            )
            if not camera_active:
                continue

            # Initialize silence start if not already tracking
            if metrics["silence_started_at"] == 0.0:
                metrics["silence_started_at"] = now
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                continue

            silence_s = now - metrics["silence_started_at"]

            # Stage 1: lightweight poke to encourage organic proactive speech.
            if (not metrics["idle_poke_sent"]) and silence_s >= ORGANIC_POKE_THRESHOLD_S:
                metrics["idle_poke_sent"] = True
                metrics["backend_pokes"] += 1
                metrics["last_poke_at"] = now
                poke_count = metrics["backend_pokes"]

                logger.info("IDLE POKE #%d — silence=%.1fs", poke_count, silence_s)
                slog("server", "idle_poke", silence_s=round(silence_s, 1), count=poke_count)

                try:
                    await _send_hidden_turn(session, IDLE_POKE_PROMPT)
                except Exception as exc:
                    metrics["idle_poke_sent"] = False
                    metrics["backend_pokes"] -= 1
                    logger.warning("Idle poke send failed: %s", exc)
                    continue
                metrics["last_hidden_prompt_at"] = now

                try:
                    await websocket.send_text(json.dumps({
                        "type": "idle_poke",
                        "data": {
                            "silence_s": round(silence_s, 1),
                            "count": poke_count,
                        },
                    }))
                except Exception:
                    pass
                continue

            # Stage 2: hard fallback nudge if soft poke did not trigger speech.
            if (not metrics["idle_nudge_sent"]) and silence_s >= HARD_NUDGE_THRESHOLD_S:
                if (
                    metrics["idle_poke_sent"]
                    and metrics["last_poke_at"] > 0
                    and (now - metrics["last_poke_at"]) < POKE_RESPONSE_GRACE_S
                ):
                    continue

                metrics["idle_nudge_sent"] = True
                metrics["backend_nudges"] += 1
                metrics["last_nudge_at"] = now
                nudge_count = metrics["backend_nudges"]
                nudge_text = IDLE_NUDGE_PROMPT.format(silence_s=int(silence_s))

                logger.info("IDLE NUDGE #%d — silence=%.1fs", nudge_count, silence_s)
                slog("server", "idle_nudge", silence_s=round(silence_s, 1), count=nudge_count)

                try:
                    await _send_hidden_turn(session, nudge_text)
                except Exception as exc:
                    metrics["idle_nudge_sent"] = False
                    metrics["backend_nudges"] -= 1
                    logger.warning("Idle nudge send failed: %s", exc)
                    continue
                metrics["last_hidden_prompt_at"] = now

                try:
                    await websocket.send_text(json.dumps({
                        "type": "idle_nudge",
                        "data": {
                            "silence_s": round(silence_s, 1),
                            "count": nudge_count,
                        },
                    }))
                except Exception:
                    pass

    except asyncio.CancelledError:
        logger.info("Session %s: idle orchestrator stopped", session_id)
    except Exception as exc:
        logger.exception("Session %s: idle orchestrator error: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Final metrics summary
# ---------------------------------------------------------------------------
def _log_final_metrics(session_id: str, metrics: dict):
    def _avg(lst):
        return sum(lst) / len(lst) if lst else 0

    avg_silence = _avg(metrics["silence_durations_s"])

    logger.info(
        "Session %s FINAL METRICS:\n"
        "  Proactive triggers=%d (organic=%d, nudge=%d)\n"
        "  Backend pokes=%d  nudges=%d\n"
        "  Internal text filtered=%d\n"
        "  Mid-session restart blocks=%d\n"
        "  Resume contexts applied=%d\n"
        "  Visual changes=%d  visual prompts=%d\n"
        "  Avg silence before trigger=%.1fs  all=%s\n"
        "  False positives=%d\n"
        "  Turns=%d  video_frames=%d  audio_in=%d  audio_out=%d",
        session_id,
        metrics["proactive_triggers"],
        metrics["organic_triggers"],
        metrics["nudge_triggers"],
        metrics["backend_pokes"],
        metrics["backend_nudges"],
        metrics["internal_text_filtered"],
        metrics["mid_session_restart_blocks"],
        metrics["resume_context_applied"],
        metrics["visual_change_events"],
        metrics["visual_change_prompts"],
        avg_silence,
        metrics["silence_durations_s"],
        metrics["false_positives"],
        metrics["turn_completes"],
        metrics["video_frames_in"],
        metrics["audio_chunks_in"],
        metrics["audio_chunks_out"],
    )
