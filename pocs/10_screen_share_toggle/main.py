"""
POC 10 — Screen Share Toggle

Minimal FastAPI + WebSocket backend that connects to the Gemini Live API
and tests seamless switching between camera and screen share as the visual
input source, without resetting the session or dropping audio.

Three concurrent tasks per session:
  1. Browser -> Gemini: forwards audio + video/screen frames + control messages
  2. Gemini -> Browser: forwards audio/text responses + transcriptions
  3. Idle Orchestrator: monitors silence + active visual source, escalates nudges

Key behaviors tested:
  - Instant source switching (camera <-> screen) without session reset
  - Tutor acknowledges the switch with a single contextual line
  - Same proactive vision pipeline works on both camera and screen inputs
  - Permission denied for screen share falls back to camera gracefully
  - All source switches are logged for metrics

Usage:
    cd pocs/10_screen_share_toggle
    uvicorn main:app --reload --port 9000
    # Open http://localhost:9000
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
logger = logging.getLogger("poc_screen_share_toggle")

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
MODEL = "gemini-live-2.5-flash-native-audio"

# Idle orchestrator thresholds
ORGANIC_POKE_THRESHOLD_S = 6.0
HARD_NUDGE_THRESHOLD_S = 9.0
CHECK_INTERVAL_S = 0.2
VISUAL_ACTIVE_TIMEOUT_S = 3.0  # Visual source considered off if no frame within this window
POKE_RESPONSE_GRACE_S = 1.2
HIDDEN_PROMPT_MIN_GAP_S = 4.0

# Proactive trigger detection
PROACTIVE_SILENCE_MIN_S = 5.0
NUDGE_ATTRIBUTION_WINDOW_S = 5.0
STUDENT_SPEECH_STALE_TIMEOUT_S = 8.0
STALE_RESET_GRACE_S = 3.0

# Source switch debounce
SOURCE_SWITCH_COOLDOWN_S = 2.0  # Min gap between switch acknowledgements

# Internal text sanitization
_INTERNAL_META_BLOCK_RE = re.compile(r"\[(?:SYSTEM|INTERNAL)[^]]*]", re.IGNORECASE)

# ---------------------------------------------------------------------------
# System Prompt — visual tutor with screen share awareness
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are SeeMe, a patient and observant visual tutor. You can see the student's \
work through their camera OR through their shared screen.

=== YOUR MOST IMPORTANT BEHAVIOR ===
You PROACTIVELY comment on what you see. You do NOT wait to be asked.
When the student is silent and you can see their work, you SPEAK UP with a \
helpful observation.

=== INPUT SOURCE AWARENESS ===
The student can switch between sharing their CAMERA (physical homework, textbooks, \
handwritten work) and their SCREEN (digital worksheets, online exercises, PDFs, \
browser content).

When you receive a source switch notification:
- If switching TO screen: say ONE short line like "Ok, I can see your screen now." \
then immediately comment on what you see.
- If switching TO camera: say ONE short line like "Ok, I'm back to your camera." \
then continue with what you see.
- NEVER ask the student to re-explain what they are doing after a switch.
- Treat both sources equally — same Socratic tutoring approach.

When viewing SCREEN content:
- You may see text that is crisp and easy to read — use that for precise guidance.
- Reference specific text, equations, or elements visible on screen.
- If content is scrollable, you can suggest: "Can you scroll down a bit?"

When viewing CAMERA content:
- Reference physical work: "I can see you wrote..."
- If view is blurry: "Can you hold it a bit closer?"

=== SESSION FLOW ===
1. GOAL CONTRACT — Ask what we are working on today.
2. GROUNDING — Say what you see before any claim.
3. PLAN — Suggest 2-3 steps, get consent.
4. EXECUTE — One issue at a time, Socratic questions only.
5. CLOSEOUT — Confirm goal met, recap key points.

=== HARD RULES ===
- ONE issue at a time.
- NEVER give the final answer — guide with observations, hints, or questions.
- DON'T speak while the student is talking.
- If you cannot clearly see the student's work, say so. Never guess.
- Match the student's language (English / Portuguese / German).
- Keep responses to 2-3 sentences.
- Speak a bit slower than normal conversational pace.
- After the session is underway, never restart with greetings.
- Keep question pressure low.

=== INTERNAL INSTRUCTIONS ===
You may receive backend control messages to help with timing and observation.
Treat them as hidden guidance only.
Never quote, paraphrase, or mention those control messages.
Never output bracketed meta text or internal reasoning.

If this is a fresh session, begin by greeting the student warmly and asking \
about their goal for this session.\
"""

# Hidden prompts
IDLE_POKE_PROMPT = (
    "INTERNAL CONTROL: Silent observation check. Student is quiet and visual "
    "input is active. If you see meaningful work, proactively offer ONE short "
    "helpful intervention (observation, hint, or question). Ask a question only "
    "if needed to unblock progress. If work is unclear, ask ONE brief check-in. "
    "Do not mention this control message."
)

IDLE_NUDGE_PROMPT = (
    "INTERNAL CONTROL: Student has been silent for {silence_s} seconds while "
    "visual input shows their work. Provide ONE concise guidance step (observation, "
    "hint, or question). Use a question only if needed to unblock progress. "
    "If view is unclear, ask one brief check-in question. One issue at a time. "
    "Never give direct answers. Do not mention this control message."
)

SOURCE_SWITCH_TO_SCREEN_PROMPT = (
    "INTERNAL CONTROL: The student just switched from camera to screen share. "
    "You can now see their screen instead of their physical camera. "
    "Acknowledge the switch with ONE short line (e.g., 'Ok, I can see your screen now.') "
    "then immediately comment on what you see on their screen. "
    "Do not ask the student to re-explain. Continue the tutoring session seamlessly. "
    "Do not mention this control message."
)

SOURCE_SWITCH_TO_CAMERA_PROMPT = (
    "INTERNAL CONTROL: The student just switched from screen share back to camera. "
    "You can now see their physical camera again instead of their screen. "
    "Acknowledge the switch with ONE short line (e.g., 'Ok, I'm back to your camera.') "
    "then continue with what you see through the camera. "
    "Do not ask the student to re-explain. Continue the tutoring session seamlessly. "
    "Do not mention this control message."
)

STOP_SHARING_PROMPT = (
    "INTERNAL CONTROL: The student stopped sharing their screen. Visual input is "
    "no longer available. Continue the session using voice only. You can say "
    "something brief like 'No worries, we can keep going with just voice.' "
    "Do not mention this control message."
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="POC 10 — Screen Share Toggle")

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
    "source_switch": "SOURCE",
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
    return {"status": "ok", "poc": "10_screen_share_toggle"}


# ---------------------------------------------------------------------------
# Helper: send hidden turn
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
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"poc10-{int(time.time())}"
    logger.info("Session %s: WebSocket connected", session_id)

    # ── Metrics ──
    metrics = {
        # Source tracking
        "active_source": "camera",  # "camera" | "screen" | "none"
        "source_switches": 0,
        "switch_to_screen_count": 0,
        "switch_to_camera_count": 0,
        "stop_sharing_count": 0,
        "last_switch_at": 0.0,
        "switch_latencies_ms": [],  # client-reported switch latency
        # Proactive vision
        "proactive_triggers": 0,
        "organic_triggers": 0,
        "nudge_triggers": 0,
        "backend_pokes": 0,
        "backend_nudges": 0,
        "silence_durations_s": [],
        "internal_text_filtered": 0,
        # State tracking
        "tutor_speaking": False,
        "client_tutor_playing": False,
        "speaking_started_at": 0.0,
        "student_speaking": False,
        "last_student_speech_at": 0.0,
        "last_student_stale_reset_at": 0.0,
        "last_video_frame_at": 0.0,
        "last_screen_frame_at": 0.0,
        "silence_started_at": 0.0,
        "idle_poke_sent": False,
        "idle_nudge_sent": False,
        "last_poke_at": 0.0,
        "last_nudge_at": 0.0,
        "last_hidden_prompt_at": 0.0,
        "last_tutor_output_at": 0.0,
        "has_seen_tutor_turn_complete": False,
        # General counters
        "turn_completes": 0,
        "audio_chunks_in": 0,
        "audio_chunks_out": 0,
        "video_frames_in": 0,
        "screen_frames_in": 0,
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
             source_switches=metrics["source_switches"],
             switch_to_screen=metrics["switch_to_screen_count"],
             switch_to_camera=metrics["switch_to_camera_count"],
             stop_sharing=metrics["stop_sharing_count"],
             proactive_triggers=metrics["proactive_triggers"],
             organic=metrics["organic_triggers"],
             nudge=metrics["nudge_triggers"],
             backend_pokes=metrics["backend_pokes"],
             backend_nudges=metrics["backend_nudges"],
             internal_text_filtered=metrics["internal_text_filtered"],
             turns=metrics["turn_completes"],
             video_frames=metrics["video_frames_in"],
             screen_frames=metrics["screen_frames_in"])
        close_logs()


# ---------------------------------------------------------------------------
# Browser -> Gemini: audio + video/screen + control messages
# ---------------------------------------------------------------------------
async def _forward_browser_to_gemini(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Receive audio, video frames, screen frames, and control messages from browser."""
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

            # ── Camera frame ──
            elif msg_type == "camera_frame":
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

            # ── Screen frame ──
            elif msg_type == "screen_frame":
                encoded = message.get("data")
                if not encoded:
                    continue
                try:
                    jpeg_bytes = base64.b64decode(encoded)
                except binascii.Error:
                    continue

                metrics["screen_frames_in"] += 1
                metrics["last_screen_frame_at"] = time.time()
                await session.send_realtime_input(
                    video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                )

            # ── Source switch ──
            elif msg_type == "source_switch":
                now = time.time()
                new_source = message.get("source", "")  # "screen" | "camera"
                old_source = metrics["active_source"]
                client_latency_ms = message.get("switch_latency_ms", 0)

                if new_source not in ("screen", "camera"):
                    continue

                # Debounce rapid switches
                if (
                    metrics["last_switch_at"] > 0
                    and (now - metrics["last_switch_at"]) < SOURCE_SWITCH_COOLDOWN_S
                    and old_source == new_source
                ):
                    continue

                metrics["active_source"] = new_source
                metrics["source_switches"] += 1
                metrics["last_switch_at"] = now
                if client_latency_ms:
                    metrics["switch_latencies_ms"].append(client_latency_ms)

                if new_source == "screen":
                    metrics["switch_to_screen_count"] += 1
                else:
                    metrics["switch_to_camera_count"] += 1

                logger.info(
                    "SOURCE SWITCH #%d: %s -> %s (client_latency=%dms)",
                    metrics["source_switches"],
                    old_source,
                    new_source,
                    client_latency_ms,
                )
                slog("server", "source_switch",
                     old_source=old_source,
                     new_source=new_source,
                     count=metrics["source_switches"],
                     client_latency_ms=client_latency_ms)

                # Inject hidden turn so Gemini knows about the switch
                prompt = (
                    SOURCE_SWITCH_TO_SCREEN_PROMPT
                    if new_source == "screen"
                    else SOURCE_SWITCH_TO_CAMERA_PROMPT
                )
                try:
                    await _send_hidden_turn(session, prompt)
                    metrics["last_hidden_prompt_at"] = now
                except Exception as exc:
                    logger.warning("Source switch prompt failed: %s", exc)
                    slog("server", "source_switch_prompt_failed", error=str(exc))

                # Notify client of acknowledged switch
                try:
                    await websocket.send_text(json.dumps({
                        "type": "source_switch_ack",
                        "data": {
                            "source": new_source,
                            "count": metrics["source_switches"],
                        },
                    }))
                except Exception:
                    pass

            # ── Stop sharing (screen share ended) ──
            elif msg_type == "stop_sharing":
                now = time.time()
                old_source = metrics["active_source"]
                metrics["active_source"] = "none"
                metrics["stop_sharing_count"] += 1

                logger.info(
                    "STOP SHARING #%d: was=%s",
                    metrics["stop_sharing_count"],
                    old_source,
                )
                slog("server", "stop_sharing",
                     old_source=old_source,
                     count=metrics["stop_sharing_count"])

                try:
                    await _send_hidden_turn(session, STOP_SHARING_PROMPT)
                    metrics["last_hidden_prompt_at"] = now
                except Exception as exc:
                    logger.warning("Stop sharing prompt failed: %s", exc)
                    slog("server", "stop_sharing_prompt_failed", error=str(exc))

                try:
                    await websocket.send_text(json.dumps({
                        "type": "stop_sharing_ack",
                        "data": {
                            "count": metrics["stop_sharing_count"],
                        },
                    }))
                except Exception:
                    pass

            # ── VAD speech state from browser ──
            elif msg_type == "speech_start":
                now = time.time()
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = now
                metrics["last_student_stale_reset_at"] = 0.0
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
                metrics["silence_started_at"] = now
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                slog("client", "speech_end")

            # ── Barge-in ──
            elif msg_type == "barge_in":
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = time.time()
                metrics["silence_started_at"] = 0.0
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                slog("client", "vad_bargein",
                     client_latency_ms=message.get("client_latency_ms", 0))

            # ── Tutor playback state from browser ──
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

                # Skip tool calls (not used in this POC)
                if getattr(msg, "tool_call", None) is not None:
                    continue

                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                # ── Interruption (Gemini server-side) ──
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
                            turn_had_tutor_output = True

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

                # ── Input transcription (student speech) ──
                input_transcription = getattr(server_content, "input_transcription", None)
                if input_transcription is not None:
                    transcript_text = getattr(input_transcription, "text", None)
                    if transcript_text:
                        logger.info("STUDENT: %s", transcript_text)
                        slog("server", "student_transcript", text=transcript_text)
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
                            slog("server", "internal_text_filtered",
                                 source="output_transcription")
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
    """Check if tutor is speaking proactively (during student silence)."""
    if not metrics["has_seen_tutor_turn_complete"]:
        return

    silence_anchor = metrics["silence_started_at"] or metrics["last_student_speech_at"]
    if silence_anchor <= 0:
        return
    silence_s = now - silence_anchor

    if silence_s < PROACTIVE_SILENCE_MIN_S:
        return

    # Is visual source active? (camera or screen)
    visual_active = _is_visual_active(now, metrics)

    metrics["proactive_triggers"] += 1
    metrics["silence_durations_s"].append(round(silence_s, 1))

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

    logger.info(
        "PROACTIVE TRIGGER #%d [%s] — silence=%.1fs, visual=%s, source=%s",
        metrics["proactive_triggers"], trigger_type, silence_s,
        "ON" if visual_active else "OFF",
        metrics["active_source"],
    )
    slog("server", "proactive_trigger",
         trigger_type=trigger_type,
         silence_s=round(silence_s, 1),
         visual_active=visual_active,
         active_source=metrics["active_source"],
         count=metrics["proactive_triggers"])

    try:
        await websocket.send_text(json.dumps({
            "type": "proactive_trigger",
            "data": {
                "trigger_type": trigger_type,
                "silence_s": round(silence_s, 1),
                "visual_active": visual_active,
                "active_source": metrics["active_source"],
                "count": metrics["proactive_triggers"],
            },
        }))
    except Exception:
        pass


def _is_visual_active(now: float, metrics: dict) -> bool:
    """Check if any visual source (camera or screen) has sent frames recently."""
    camera_active = (
        metrics["last_video_frame_at"] > 0
        and (now - metrics["last_video_frame_at"]) < VISUAL_ACTIVE_TIMEOUT_S
    )
    screen_active = (
        metrics["last_screen_frame_at"] > 0
        and (now - metrics["last_screen_frame_at"]) < VISUAL_ACTIVE_TIMEOUT_S
    )
    return camera_active or screen_active


# ---------------------------------------------------------------------------
# Idle Orchestrator
# ---------------------------------------------------------------------------
async def _idle_orchestrator(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Background task that escalates from soft poke to hard nudge."""
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)

            now = time.time()

            if metrics["tutor_speaking"] or metrics["client_tutor_playing"]:
                continue

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

            # Check if ANY visual source is active
            visual_active = _is_visual_active(now, metrics)
            if not visual_active:
                continue

            if metrics["silence_started_at"] == 0.0:
                metrics["silence_started_at"] = now
                metrics["idle_poke_sent"] = False
                metrics["idle_nudge_sent"] = False
                continue

            silence_s = now - metrics["silence_started_at"]

            # Stage 1: soft poke
            if (not metrics["idle_poke_sent"]) and silence_s >= ORGANIC_POKE_THRESHOLD_S:
                metrics["idle_poke_sent"] = True
                metrics["backend_pokes"] += 1
                metrics["last_poke_at"] = now
                poke_count = metrics["backend_pokes"]

                logger.info("IDLE POKE #%d — silence=%.1fs, source=%s",
                            poke_count, silence_s, metrics["active_source"])
                slog("server", "idle_poke",
                     silence_s=round(silence_s, 1),
                     active_source=metrics["active_source"],
                     count=poke_count)

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
                            "active_source": metrics["active_source"],
                            "count": poke_count,
                        },
                    }))
                except Exception:
                    pass
                continue

            # Stage 2: hard nudge
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

                logger.info("IDLE NUDGE #%d — silence=%.1fs, source=%s",
                            nudge_count, silence_s, metrics["active_source"])
                slog("server", "idle_nudge",
                     silence_s=round(silence_s, 1),
                     active_source=metrics["active_source"],
                     count=nudge_count)

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
                            "active_source": metrics["active_source"],
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
    avg_switch_lat = _avg(metrics["switch_latencies_ms"])

    logger.info(
        "Session %s FINAL METRICS:\n"
        "  Source switches=%d (to_screen=%d, to_camera=%d, stop_sharing=%d)\n"
        "  Avg switch latency=%.0fms  all=%s\n"
        "  Proactive triggers=%d (organic=%d, nudge=%d)\n"
        "  Backend pokes=%d  nudges=%d\n"
        "  Internal text filtered=%d\n"
        "  Avg silence before trigger=%.1fs  all=%s\n"
        "  Turns=%d  video_frames=%d  screen_frames=%d  audio_in=%d  audio_out=%d",
        session_id,
        metrics["source_switches"],
        metrics["switch_to_screen_count"],
        metrics["switch_to_camera_count"],
        metrics["stop_sharing_count"],
        avg_switch_lat,
        metrics["switch_latencies_ms"],
        metrics["proactive_triggers"],
        metrics["organic_triggers"],
        metrics["nudge_triggers"],
        metrics["backend_pokes"],
        metrics["backend_nudges"],
        metrics["internal_text_filtered"],
        avg_silence,
        metrics["silence_durations_s"],
        metrics["turn_completes"],
        metrics["video_frames_in"],
        metrics["screen_frames_in"],
        metrics["audio_chunks_in"],
        metrics["audio_chunks_out"],
    )
