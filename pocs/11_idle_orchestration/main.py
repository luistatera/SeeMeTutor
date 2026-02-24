"""
POC 11 — Idle Orchestration

Minimal FastAPI + WebSocket backend that connects to the Gemini Live API
and tests a server-driven idle state machine for natural silence handling.

Four idle states:
  ACTIVE     — Student is engaged, timers reset on any user audio
  GENTLE_CHECK — 10s silence: one short calm check-in sentence
  OFFER_OPTIONS — 25s silence: offer repeat / hint / break options
  AWAY       — 90s silence: stop talking entirely, wait silently

Three concurrent tasks per session:
  1. Browser -> Gemini: forwards audio + speech state
  2. Gemini -> Browser: forwards audio/text responses + transcriptions
  3. Idle Orchestrator: monitors silence, manages state machine, injects prompts

Key behaviors tested:
  - Idle state transitions happen at exact thresholds
  - Maximum 1 prompt per stage, then silence (no nagging)
  - Interrupt-safe: user speech instantly resets to ACTIVE
  - Voice commands: "give me a moment" -> AWAY; "I'm back" -> resume
  - UI displays visible state: Active, Waiting, Away, Resuming

Usage:
    cd pocs/11_idle_orchestration
    uvicorn main:app --reload --port 9100
    # Open http://localhost:9100
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
logger = logging.getLogger("poc_idle_orchestration")

# ---------------------------------------------------------------------------
# Gemini backend: Vertex AI
# ---------------------------------------------------------------------------
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT_ID", "seeme-tutor"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_REGION", "europe-west1"))

from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL = "gemini-live-2.5-flash-native-audio"

# Idle state machine thresholds (seconds of silence)
GENTLE_CHECK_THRESHOLD_S = 10.0
OFFER_OPTIONS_THRESHOLD_S = 25.0
AWAY_THRESHOLD_S = 90.0

# Orchestrator loop interval
CHECK_INTERVAL_S = 0.25

# State enum values
STATE_ACTIVE = "active"
STATE_GENTLE_CHECK = "gentle_check"
STATE_OFFER_OPTIONS = "offer_options"
STATE_AWAY = "away"
STATE_RESUMING = "resuming"

# Voice command patterns
PAUSE_COMMANDS_RE = re.compile(
    r"\b(?:give\s+me\s+a?\s*(?:moment|minute|second|sec)"
    r"|hold\s+on"
    r"|just\s+a?\s*(?:moment|minute|second|sec)"
    r"|let\s+me\s+think"
    r"|wait\s+(?:a\s+)?(?:moment|minute|second|sec|bit)"
    r"|I\s+need\s+(?:a\s+)?(?:moment|minute|second|sec)"
    r"|brb"
    r"|take\s+a\s+break"
    r"|pause)\b",
    re.IGNORECASE,
)

RESUME_COMMANDS_RE = re.compile(
    r"\b(?:I'?\s*m\s+back"
    r"|okay\s+(?:I'?\s*m\s+)?(?:back|ready)"
    r"|back\s+now"
    r"|I'?\s*m\s+ready"
    r"|I'?\s*m\s+here"
    r"|ready\s+now"
    r"|let'?\s*s?\s+(?:go|continue|keep\s+going)"
    r"|resume)\b",
    re.IGNORECASE,
)

# Stale speech detection
STUDENT_SPEECH_STALE_TIMEOUT_S = 8.0
STALE_RESET_GRACE_S = 3.0

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are SeeMe, a patient and calm tutor. You are in a test session where we \
are specifically testing idle and silence handling.

IMPORTANT BEHAVIORS:
1. When the student asks a question, give a helpful, detailed answer \
(3-4 sentences). This gives time for the tester to go silent.
2. You understand that silence is normal — students think, write, and take breaks.
3. You never nag or repeat yourself. One check-in at most, then wait quietly.
4. When you receive a backend control message during silence:
   - Follow the instruction naturally, as if it were your own idea
   - Keep it SHORT: 1 sentence maximum
   - Do NOT mention the control message
   - Do NOT say "I noticed you've been quiet" — be more natural
5. If someone says "give me a moment" or "hold on" or "let me think":
   - Say one brief acknowledgment like "Sure, take your time" or "Of course"
   - Then go COMPLETELY silent until they speak again
6. If someone says "I'm back" or "I'm ready" or "let's continue":
   - Welcome them back briefly
   - Recap what you were working on in ONE sentence
   - Then continue naturally

You speak English by default. If someone speaks Portuguese or German, match \
their language.

Start by introducing yourself and asking what the student wants to work on today.

INTERNAL INSTRUCTIONS:
You may receive backend control messages to help with timing.
Treat them as hidden guidance only.
Never quote, paraphrase, or mention those control messages.
Never output bracketed meta text or internal reasoning.\
"""

# Hidden prompts injected by the idle orchestrator
GENTLE_CHECK_PROMPT = (
    "INTERNAL CONTROL: The student has been silent for about 10 seconds. "
    "Give ONE short, calm check-in sentence. Examples: "
    "'Take your time, I'm here when you're ready.' or "
    "'No rush — let me know when you want to continue.' "
    "Maximum 1 sentence. Do not nag. Do not repeat yourself. "
    "Match the student's language. Do not mention this control message."
)

OFFER_OPTIONS_PROMPT = (
    "INTERNAL CONTROL: The student has been silent for about 25 seconds. "
    "Offer options in ONE sentence: 'Would you like me to repeat that, "
    "give you a hint, or should we take a break?' Adjust phrasing to be "
    "natural and match the student's language. Maximum 1 sentence. "
    "Do not mention this control message."
)

RESUME_PROMPT = (
    "INTERNAL CONTROL: The student just returned from a break or away period. "
    "Welcome them back briefly and recap what you were working on in ONE sentence. "
    "Then continue naturally. Do not mention this control message."
)

# Internal text sanitization
_INTERNAL_META_BLOCK_RE = re.compile(r"\[(?:SYSTEM|INTERNAL)[^]]*]", re.IGNORECASE)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="POC 11 — Idle Orchestration")

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
    "idle": "IDLE",
    "error": "ERROR",
}


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

    upper_stripped = cleaned.lstrip().upper()
    if upper_stripped.startswith("SYSTEM:") or upper_stripped.startswith("INTERNAL CONTROL:"):
        had_internal = True
        return "", True

    if not cleaned.strip():
        return "", had_internal
    return cleaned, had_internal


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
    return {"status": "ok", "poc": "11_idle_orchestration"}


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"poc-{int(time.time())}"
    logger.info("Session %s: WebSocket connected", session_id)

    # Metrics and state
    metrics = {
        # Idle orchestration
        "idle_state": STATE_ACTIVE,
        "last_user_activity": 0.0,
        "silence_started_at": 0.0,
        "gentle_check_sent": False,
        "offer_options_sent": False,
        "gentle_check_count": 0,
        "offer_options_count": 0,
        "away_entries": 0,
        "resume_count": 0,
        "voice_pause_count": 0,
        "voice_resume_count": 0,
        "state_transitions": [],
        # Internal text filtering
        "internal_text_filtered": 0,
        # Speech state
        "tutor_speaking": False,
        "client_tutor_playing": False,
        "speaking_started_at": 0.0,
        "student_speaking": False,
        "last_student_speech_at": 0.0,
        "last_student_stale_reset_at": 0.0,
        "last_tutor_output_at": 0.0,
        # General counters
        "turn_completes": 0,
        "audio_chunks_in": 0,
        "audio_chunks_out": 0,
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
             idle_state=metrics["idle_state"],
             gentle_checks=metrics["gentle_check_count"],
             offer_options=metrics["offer_options_count"],
             away_entries=metrics["away_entries"],
             resumes=metrics["resume_count"],
             voice_pauses=metrics["voice_pause_count"],
             voice_resumes=metrics["voice_resume_count"],
             internal_text_filtered=metrics["internal_text_filtered"],
             turns=metrics["turn_completes"],
             transitions=len(metrics["state_transitions"]))
        close_logs()


# ---------------------------------------------------------------------------
# Idle state machine helpers
# ---------------------------------------------------------------------------
def _transition_idle_state(
    metrics: dict,
    new_state: str,
    slog,
    reason: str = "",
):
    """Transition the idle state machine and log it."""
    old_state = metrics["idle_state"]
    if old_state == new_state:
        return

    now = time.time()
    metrics["idle_state"] = new_state
    transition = {
        "from": old_state,
        "to": new_state,
        "at": now,
        "reason": reason,
    }
    metrics["state_transitions"].append(transition)

    logger.info(
        "IDLE STATE: %s -> %s (reason: %s)",
        old_state, new_state, reason,
    )
    slog("server", "idle_state_transition",
         from_state=old_state,
         to_state=new_state,
         reason=reason,
         transition_count=len(metrics["state_transitions"]))


async def _send_idle_state_to_client(websocket: WebSocket, state: str, metrics: dict):
    """Send idle state change to the frontend."""
    # Map internal states to UI-friendly labels
    ui_state_map = {
        STATE_ACTIVE: "active",
        STATE_GENTLE_CHECK: "waiting",
        STATE_OFFER_OPTIONS: "waiting",
        STATE_AWAY: "away",
        STATE_RESUMING: "resuming",
    }
    ui_state = ui_state_map.get(state, state)

    try:
        await websocket.send_text(json.dumps({
            "type": "idle_state",
            "state": ui_state,
            "detail": state,
            "silence_s": round(
                time.time() - metrics["silence_started_at"], 1
            ) if metrics["silence_started_at"] > 0 else 0,
        }))
    except Exception:
        pass


def _reset_to_active(metrics: dict, slog, reason: str = "user_activity"):
    """Reset all idle timers and return to ACTIVE state."""
    was_away = metrics["idle_state"] in (STATE_AWAY, STATE_OFFER_OPTIONS, STATE_GENTLE_CHECK)
    _transition_idle_state(metrics, STATE_ACTIVE, slog, reason)
    metrics["silence_started_at"] = 0.0
    metrics["gentle_check_sent"] = False
    metrics["offer_options_sent"] = False
    return was_away


# ---------------------------------------------------------------------------
# Browser -> Gemini: audio + speech state + voice commands
# ---------------------------------------------------------------------------
async def _forward_browser_to_gemini(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Receive audio and control messages from the browser."""
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = message.get("type")

            # -- Audio --
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

            # -- VAD speech state from browser --
            elif msg_type == "speech_start":
                now = time.time()
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = now
                metrics["last_user_activity"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                # Reset idle state to ACTIVE immediately
                was_away = _reset_to_active(metrics, slog, "speech_start")
                if was_away:
                    await _send_idle_state_to_client(websocket, STATE_ACTIVE, metrics)
                slog("client", "speech_start")

            elif msg_type == "speech_keepalive":
                now = time.time()
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = now
                metrics["last_user_activity"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                if metrics["idle_state"] != STATE_ACTIVE:
                    _reset_to_active(metrics, slog, "speech_keepalive")
                    await _send_idle_state_to_client(websocket, STATE_ACTIVE, metrics)

            elif msg_type == "speech_end":
                now = time.time()
                metrics["student_speaking"] = False
                metrics["last_student_speech_at"] = now
                metrics["last_user_activity"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                # Start silence window for idle orchestrator
                metrics["silence_started_at"] = now
                metrics["gentle_check_sent"] = False
                metrics["offer_options_sent"] = False
                slog("client", "speech_end")

            # -- Barge-in --
            elif msg_type == "barge_in":
                now = time.time()
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = now
                metrics["last_user_activity"] = now
                metrics["last_student_stale_reset_at"] = 0.0
                was_away = _reset_to_active(metrics, slog, "barge_in")
                if was_away:
                    await _send_idle_state_to_client(websocket, STATE_ACTIVE, metrics)
                slog("client", "vad_bargein",
                     client_latency_ms=message.get("client_latency_ms", 0))

            # -- Tutor playback state from browser --
            elif msg_type == "tutor_playback_start":
                now = time.time()
                metrics["client_tutor_playing"] = True
                metrics["tutor_speaking"] = True
                metrics["speaking_started_at"] = now
                metrics["silence_started_at"] = 0.0
                metrics["gentle_check_sent"] = False
                metrics["offer_options_sent"] = False
                slog("client", "tutor_playback_start")

            elif msg_type == "tutor_playback_end":
                now = time.time()
                metrics["client_tutor_playing"] = False
                metrics["tutor_speaking"] = False
                metrics["speaking_started_at"] = 0.0
                if not metrics["student_speaking"]:
                    metrics["silence_started_at"] = now
                    metrics["gentle_check_sent"] = False
                    metrics["offer_options_sent"] = False
                slog("client", "tutor_playback_end")

            # -- Manual idle controls from UI --
            elif msg_type == "take_break":
                now = time.time()
                metrics["last_user_activity"] = now
                _transition_idle_state(metrics, STATE_AWAY, slog, "manual_break_button")
                metrics["away_entries"] += 1
                metrics["silence_started_at"] = now
                metrics["gentle_check_sent"] = True
                metrics["offer_options_sent"] = True
                await _send_idle_state_to_client(websocket, STATE_AWAY, metrics)
                slog("client", "manual_break")

            elif msg_type == "im_back":
                now = time.time()
                metrics["last_user_activity"] = now
                metrics["resume_count"] += 1
                _transition_idle_state(metrics, STATE_RESUMING, slog, "manual_resume_button")
                await _send_idle_state_to_client(websocket, STATE_RESUMING, metrics)
                # Inject resume prompt
                try:
                    await _send_hidden_turn(session, RESUME_PROMPT)
                except Exception as exc:
                    logger.warning("Resume prompt send failed: %s", exc)
                # Transition to active after sending resume prompt
                _transition_idle_state(metrics, STATE_ACTIVE, slog, "resume_complete")
                metrics["silence_started_at"] = 0.0
                metrics["gentle_check_sent"] = False
                metrics["offer_options_sent"] = False
                await _send_idle_state_to_client(websocket, STATE_ACTIVE, metrics)
                slog("client", "manual_resume")

            # -- Client-side event logging --
            elif msg_type == "client_log":
                slog("client", message.get("event", "log"),
                     text=message.get("text", ""),
                     **{k: v for k, v in message.items()
                        if k not in ("type", "event", "text")})

            # -- Activity signals (for Gemini's VAD) --
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
# Gemini -> Browser: audio, text, interruptions, transcriptions
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

    try:
        while True:
            turn_index += 1
            turn_events = 0

            async for msg in session.receive():
                turn_events += 1

                if getattr(msg, "tool_call", None) is not None:
                    continue

                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                # -- Interruption (Gemini server-side) --
                if getattr(server_content, "interrupted", False):
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

                # -- Turn complete --
                turn_complete = getattr(server_content, "turn_complete", False)

                # -- Audio / text content --
                model_turn = getattr(server_content, "model_turn", None)
                if model_turn is not None:
                    parts = getattr(model_turn, "parts", None) or []
                    for part in parts:
                        # Audio output
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data is not None and inline_data.data:
                            now = time.time()
                            turn_had_tutor_output = True

                            if not metrics["tutor_speaking"]:
                                metrics["tutor_speaking"] = True
                                metrics["speaking_started_at"] = now

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
                                slog("server", "internal_text_filtered",
                                     source="model_turn_text")
                            if safe_text:
                                turn_had_tutor_output = True
                                metrics["last_tutor_output_at"] = time.time()
                                logger.info("TUTOR: %s", safe_text)
                                slog("server", "tutor_text", text=safe_text)
                                await websocket.send_text(json.dumps({
                                    "type": "text",
                                    "data": safe_text,
                                }))

                # -- Input transcription (student speech) --
                input_transcription = getattr(server_content, "input_transcription", None)
                if input_transcription is not None:
                    transcript_text = getattr(input_transcription, "text", None)
                    if transcript_text:
                        logger.info("STUDENT: %s", transcript_text)
                        slog("server", "student_transcript", text=transcript_text)
                        metrics["last_student_speech_at"] = time.time()
                        metrics["last_user_activity"] = time.time()
                        await websocket.send_text(json.dumps({
                            "type": "input_transcript",
                            "data": transcript_text,
                        }))

                        # Check for voice commands in transcription
                        await _check_voice_commands(
                            websocket, session, transcript_text,
                            metrics, slog
                        )

                # -- Output transcription (tutor speech) --
                output_transcription = getattr(server_content, "output_transcription", None)
                if output_transcription is not None:
                    transcript_text = getattr(output_transcription, "text", None)
                    if transcript_text:
                        safe_transcript, had_internal = _sanitize_tutor_output(transcript_text)
                        if had_internal:
                            metrics["internal_text_filtered"] += 1
                            slog("server", "internal_text_filtered",
                                 source="output_transcription")
                        if safe_transcript:
                            turn_had_tutor_output = True
                            metrics["last_tutor_output_at"] = time.time()
                            await websocket.send_text(json.dumps({
                                "type": "output_transcript",
                                "data": safe_transcript,
                            }))

                # -- Turn complete --
                if turn_complete:
                    metrics["turn_completes"] += 1
                    metrics["tutor_speaking"] = False
                    metrics["speaking_started_at"] = 0.0

                    # Start silence window if tutor actually spoke
                    if turn_had_tutor_output and not metrics["student_speaking"]:
                        metrics["silence_started_at"] = time.time()
                        metrics["gentle_check_sent"] = False
                        metrics["offer_options_sent"] = False

                    logger.info("TURN COMPLETE #%d", metrics["turn_completes"])
                    slog("server", "turn_complete", count=metrics["turn_completes"])
                    await websocket.send_text(json.dumps({
                        "type": "turn_complete",
                        "data": {"count": metrics["turn_completes"]},
                    }))
                    turn_had_tutor_output = False

            if turn_events == 0:
                logger.info("Session %s: Gemini stream ended", session_id)
                return
            await asyncio.sleep(0)

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected (receive)", session_id)
    except Exception as exc:
        logger.exception("Session %s: receive error: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Voice command detection
# ---------------------------------------------------------------------------
async def _check_voice_commands(
    websocket: WebSocket,
    session,
    transcript: str,
    metrics: dict,
    slog,
):
    """Check transcribed text for pause/resume voice commands."""
    # Check for pause commands
    if PAUSE_COMMANDS_RE.search(transcript):
        metrics["voice_pause_count"] += 1
        logger.info("VOICE COMMAND: pause detected in '%s'", transcript)
        slog("server", "voice_command_pause",
             text=transcript, count=metrics["voice_pause_count"])

        _transition_idle_state(metrics, STATE_AWAY, slog, "voice_command_pause")
        metrics["away_entries"] += 1
        metrics["silence_started_at"] = time.time()
        metrics["gentle_check_sent"] = True
        metrics["offer_options_sent"] = True
        await _send_idle_state_to_client(websocket, STATE_AWAY, metrics)
        return

    # Check for resume commands
    if RESUME_COMMANDS_RE.search(transcript):
        if metrics["idle_state"] in (STATE_AWAY, STATE_OFFER_OPTIONS, STATE_GENTLE_CHECK):
            metrics["voice_resume_count"] += 1
            logger.info("VOICE COMMAND: resume detected in '%s'", transcript)
            slog("server", "voice_command_resume",
                 text=transcript, count=metrics["voice_resume_count"])

            metrics["resume_count"] += 1
            _transition_idle_state(metrics, STATE_RESUMING, slog, "voice_command_resume")
            await _send_idle_state_to_client(websocket, STATE_RESUMING, metrics)

            # Inject resume prompt
            try:
                await _send_hidden_turn(session, RESUME_PROMPT)
            except Exception as exc:
                logger.warning("Resume prompt send failed: %s", exc)

            _transition_idle_state(metrics, STATE_ACTIVE, slog, "resume_complete")
            metrics["silence_started_at"] = 0.0
            metrics["gentle_check_sent"] = False
            metrics["offer_options_sent"] = False
            await _send_idle_state_to_client(websocket, STATE_ACTIVE, metrics)


# ---------------------------------------------------------------------------
# Hidden turn injection
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


# ---------------------------------------------------------------------------
# Idle Orchestrator — monitors silence and manages state machine
# ---------------------------------------------------------------------------
async def _idle_orchestrator(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Background task that monitors silence and transitions idle states.

    State machine:
      ACTIVE (default)
        |-- 10s silence --> GENTLE_CHECK (send 1 check-in, then wait)
        |-- 25s silence --> OFFER_OPTIONS (send 1 options prompt, then wait)
        |-- 90s silence --> AWAY (stop talking entirely)
        |-- user speaks --> ACTIVE (instant reset)

    Rules:
      - Maximum 1 prompt per stage, then silence
      - If user speaks at any point, immediately return to ACTIVE
      - In AWAY mode, agent stays completely quiet
    """
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)

            now = time.time()

            # Don't run idle logic while tutor is speaking
            if metrics["tutor_speaking"] or metrics["client_tutor_playing"]:
                continue

            # Don't run idle logic while student is speaking
            if metrics["student_speaking"]:
                # Check for stale speech flag
                stale_s = now - metrics["last_student_speech_at"]
                if stale_s > STUDENT_SPEECH_STALE_TIMEOUT_S:
                    metrics["student_speaking"] = False
                    metrics["last_student_stale_reset_at"] = now
                    metrics["silence_started_at"] = now
                    metrics["gentle_check_sent"] = False
                    metrics["offer_options_sent"] = False
                    slog("server", "student_speaking_stale_reset",
                         stale_s=round(stale_s, 1))
                else:
                    continue

            # Grace period after stale reset
            if (
                metrics["last_student_stale_reset_at"] > 0
                and (now - metrics["last_student_stale_reset_at"]) < STALE_RESET_GRACE_S
            ):
                continue

            # Already in AWAY — stay quiet, do nothing
            if metrics["idle_state"] == STATE_AWAY:
                continue

            # No silence window started yet
            if metrics["silence_started_at"] <= 0:
                continue

            silence_s = now - metrics["silence_started_at"]

            # Send current silence duration to client periodically for the timer
            if int(silence_s) > 0 and int(silence_s * 4) % 4 == 0:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "silence_tick",
                        "silence_s": round(silence_s, 1),
                    }))
                except Exception:
                    pass

            # Stage 3: AWAY mode (90s)
            if silence_s >= AWAY_THRESHOLD_S and metrics["idle_state"] != STATE_AWAY:
                _transition_idle_state(metrics, STATE_AWAY, slog, "silence_90s")
                metrics["away_entries"] += 1
                await _send_idle_state_to_client(websocket, STATE_AWAY, metrics)

                slog("server", "idle_away",
                     silence_s=round(silence_s, 1),
                     count=metrics["away_entries"])
                continue

            # Stage 2: Offer options (25s)
            if (
                silence_s >= OFFER_OPTIONS_THRESHOLD_S
                and not metrics["offer_options_sent"]
            ):
                metrics["offer_options_sent"] = True
                metrics["offer_options_count"] += 1

                _transition_idle_state(metrics, STATE_OFFER_OPTIONS, slog, "silence_25s")
                await _send_idle_state_to_client(websocket, STATE_OFFER_OPTIONS, metrics)

                logger.info(
                    "IDLE OFFER OPTIONS #%d — silence=%.1fs",
                    metrics["offer_options_count"], silence_s,
                )
                slog("server", "idle_offer_options",
                     silence_s=round(silence_s, 1),
                     count=metrics["offer_options_count"])

                try:
                    await _send_hidden_turn(session, OFFER_OPTIONS_PROMPT)
                except Exception as exc:
                    metrics["offer_options_sent"] = False
                    metrics["offer_options_count"] -= 1
                    logger.warning("Offer options prompt send failed: %s", exc)
                continue

            # Stage 1: Gentle check-in (10s)
            if (
                silence_s >= GENTLE_CHECK_THRESHOLD_S
                and not metrics["gentle_check_sent"]
            ):
                metrics["gentle_check_sent"] = True
                metrics["gentle_check_count"] += 1

                _transition_idle_state(metrics, STATE_GENTLE_CHECK, slog, "silence_10s")
                await _send_idle_state_to_client(websocket, STATE_GENTLE_CHECK, metrics)

                logger.info(
                    "IDLE GENTLE CHECK #%d — silence=%.1fs",
                    metrics["gentle_check_count"], silence_s,
                )
                slog("server", "idle_gentle_check",
                     silence_s=round(silence_s, 1),
                     count=metrics["gentle_check_count"])

                try:
                    await _send_hidden_turn(session, GENTLE_CHECK_PROMPT)
                except Exception as exc:
                    metrics["gentle_check_sent"] = False
                    metrics["gentle_check_count"] -= 1
                    logger.warning("Gentle check prompt send failed: %s", exc)
                continue

    except asyncio.CancelledError:
        logger.info("Session %s: idle orchestrator stopped", session_id)
    except Exception as exc:
        logger.exception("Session %s: idle orchestrator error: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Final metrics summary
# ---------------------------------------------------------------------------
def _log_final_metrics(session_id: str, metrics: dict):
    logger.info(
        "Session %s FINAL METRICS:\n"
        "  Idle states: gentle_checks=%d  offer_options=%d  away_entries=%d\n"
        "  Resumes=%d  voice_pauses=%d  voice_resumes=%d\n"
        "  State transitions=%d\n"
        "  Internal text filtered=%d\n"
        "  Turns=%d  audio_in=%d  audio_out=%d",
        session_id,
        metrics["gentle_check_count"],
        metrics["offer_options_count"],
        metrics["away_entries"],
        metrics["resume_count"],
        metrics["voice_pause_count"],
        metrics["voice_resume_count"],
        len(metrics["state_transitions"]),
        metrics["internal_text_filtered"],
        metrics["turn_completes"],
        metrics["audio_chunks_in"],
        metrics["audio_chunks_out"],
    )
