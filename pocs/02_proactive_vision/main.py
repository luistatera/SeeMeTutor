"""
POC 02 — Proactive Vision

Minimal FastAPI + WebSocket backend that connects to the Gemini Live API
and tests the tutor's ability to proactively comment on visual content
without being asked, using a goal-driven mission-control flow.

Three concurrent tasks per session:
  1. Browser → Gemini: forwards audio + video frames
  2. Gemini → Browser: forwards audio/text responses + detects proactive triggers
  3. Idle Orchestrator: monitors silence + camera, injects nudge prompts

Key behaviors tested:
  - Proactive trigger: tutor speaks up during student silence with visible work
  - Progressive disclosure: one issue at a time
  - Goal-driven flow: Goal → Grounding → Plan → Execute → Closeout
  - Backend idle nudge: hidden prompt after configurable silence threshold

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
IDLE_THRESHOLD_S = 15.0          # Seconds of silence before backend nudge
CHECK_INTERVAL_S = 1.0           # How often the idle loop checks
CAMERA_ACTIVE_TIMEOUT_S = 3.0   # Camera considered off if no frame within this window

# Proactive trigger detection
PROACTIVE_SILENCE_MIN_S = 5.0   # Min silence to count tutor speech as "proactive"
NUDGE_ATTRIBUTION_WINDOW_S = 5.0 # If nudge was sent within this window, attribute trigger to it

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
   - Observe camera → find ONE relevant issue → ask ONE Socratic question.
   - Wait for student attempt → verify from the updated camera view.
   - Move to next issue only after current one is resolved.

5. CLOSEOUT (when done-criteria met)
   - Confirm goal met → recap 1–3 key points → offer next goal.

═══ HARD RULES ═══
• ONE issue at a time — never list multiple problems.
• NEVER give the answer — always guide with questions.
• DON'T speak while the student is talking — listen first.
• Always reference what you SEE: "Looking at your work, I notice..."
• If camera shows nothing relevant, ask a brief check-in instead.
• Match the student's language (English / Portuguese / German).
• Keep responses to 2–3 sentences.

═══ INTERNAL INSTRUCTIONS ═══
Messages starting with [SYSTEM:] are internal guidance. Act on them naturally \
but never acknowledge, repeat, or reference them to the student.

Begin by greeting the student warmly and asking about their goal for this session.\
"""

# Hidden prompt injected by the idle orchestrator after silence threshold
IDLE_NUDGE_PROMPT = (
    "[SYSTEM: The student has been silent for {silence_s} seconds while the "
    "camera shows their work. Look at what you can see and provide ONE piece "
    "of Socratic guidance aligned with the session goal. If you cannot see "
    "any work or the view is unclear, ask a brief check-in question. "
    "Remember: one issue at a time, never give the answer directly.]"
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
            transcript_lines.append(f"{ts_short}{label}: {text}")
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
        "backend_nudges": 0,
        "silence_durations_s": [],
        "false_positives": 0,
        # State tracking
        "tutor_speaking": False,
        "speaking_started_at": 0.0,
        "student_speaking": False,
        "last_student_speech_at": 0.0,
        "last_video_frame_at": 0.0,
        "silence_started_at": 0.0,
        "idle_prompt_sent": False,
        "last_nudge_at": 0.0,
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
             backend_nudges=metrics["backend_nudges"],
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
                # Reset idle orchestrator state
                metrics["silence_started_at"] = 0.0
                metrics["idle_prompt_sent"] = False
                slog("client", "speech_start")

            elif msg_type == "speech_end":
                now = time.time()
                metrics["student_speaking"] = False
                metrics["last_student_speech_at"] = now
                # Start silence window
                metrics["silence_started_at"] = now
                metrics["idle_prompt_sent"] = False
                slog("client", "speech_end")

            # ── Barge-in (basic interruption support) ──
            elif msg_type == "barge_in":
                metrics["student_speaking"] = True
                metrics["last_student_speech_at"] = time.time()
                metrics["silence_started_at"] = 0.0
                metrics["idle_prompt_sent"] = False
                slog("client", "vad_bargein",
                     client_latency_ms=message.get("client_latency_ms", 0))

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

                            # Detect proactive trigger on first audio chunk
                            if not metrics["tutor_speaking"]:
                                metrics["tutor_speaking"] = True
                                metrics["speaking_started_at"] = now
                                await _check_proactive_trigger(
                                    now, metrics, slog, websocket
                                )

                            metrics["audio_chunks_out"] += 1
                            metrics["last_audio_out_at"] = now

                            encoded = base64.b64encode(inline_data.data).decode("utf-8")
                            await websocket.send_text(json.dumps({
                                "type": "audio",
                                "data": encoded,
                            }))

                        # Text output
                        text = getattr(part, "text", None)
                        if text:
                            logger.info("TUTOR: %s", text)
                            slog("server", "tutor_text", text=text)
                            await websocket.send_text(json.dumps({
                                "type": "text",
                                "data": text,
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
                        await websocket.send_text(json.dumps({
                            "type": "output_transcript",
                            "data": transcript_text,
                        }))

                # ── Turn complete ──
                if turn_complete:
                    metrics["turn_completes"] += 1
                    metrics["tutor_speaking"] = False
                    metrics["speaking_started_at"] = 0.0

                    # Start silence window after tutor finishes
                    if not metrics["student_speaking"]:
                        metrics["silence_started_at"] = time.time()
                        metrics["idle_prompt_sent"] = False

                    logger.info("TURN COMPLETE #%d", metrics["turn_completes"])
                    slog("server", "turn_complete", count=metrics["turn_completes"])
                    await websocket.send_text(json.dumps({
                        "type": "turn_complete",
                        "data": {"count": metrics["turn_completes"]},
                    }))

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
    # How long has the student been silent?
    if metrics["last_student_speech_at"] > 0:
        silence_s = now - metrics["last_student_speech_at"]
    else:
        silence_s = 999.0  # Student never spoke yet (session start greeting)

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
async def _idle_orchestrator(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Background task that nudges Gemini when the student is silent.

    When the student has been silent for IDLE_THRESHOLD_S seconds and the
    camera is actively sending frames, inject a hidden text prompt to Gemini
    asking it to analyze the current video and provide guidance.

    This is the "S1: Backend Forced Trigger" from the PRD — a fallback when
    the system prompt alone doesn't produce proactive behavior.
    """
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)

            now = time.time()

            # Don't nudge if tutor is currently speaking
            if metrics["tutor_speaking"]:
                continue

            # Don't nudge if student is currently speaking
            if metrics["student_speaking"]:
                metrics["silence_started_at"] = 0.0
                metrics["idle_prompt_sent"] = False
                continue

            # Check if camera is active (received frame recently)
            camera_active = (
                metrics["last_video_frame_at"] > 0
                and (now - metrics["last_video_frame_at"]) < CAMERA_ACTIVE_TIMEOUT_S
            )
            if not camera_active:
                continue

            # Already nudged for this silence window
            if metrics["idle_prompt_sent"]:
                continue

            # Initialize silence start if not already tracking
            if metrics["silence_started_at"] == 0.0:
                metrics["silence_started_at"] = now
                continue

            silence_s = now - metrics["silence_started_at"]

            if silence_s >= IDLE_THRESHOLD_S:
                metrics["idle_prompt_sent"] = True
                metrics["backend_nudges"] += 1
                metrics["last_nudge_at"] = now

                nudge_text = IDLE_NUDGE_PROMPT.format(silence_s=int(silence_s))

                logger.info(
                    "IDLE NUDGE #%d — silence=%.1fs, injecting prompt",
                    metrics["backend_nudges"], silence_s,
                )
                slog("server", "idle_nudge",
                     silence_s=round(silence_s, 1),
                     count=metrics["backend_nudges"])

                try:
                    await session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[types.Part(text=nudge_text)],
                        ),
                        turn_complete=True,
                    )
                except Exception as exc:
                    logger.warning("Idle nudge send failed: %s", exc)
                    continue

                # Notify frontend
                try:
                    await websocket.send_text(json.dumps({
                        "type": "idle_nudge",
                        "data": {
                            "silence_s": round(silence_s, 1),
                            "count": metrics["backend_nudges"],
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
        "  Backend nudges=%d\n"
        "  Avg silence before trigger=%.1fs  all=%s\n"
        "  False positives=%d\n"
        "  Turns=%d  video_frames=%d  audio_in=%d  audio_out=%d",
        session_id,
        metrics["proactive_triggers"],
        metrics["organic_triggers"],
        metrics["nudge_triggers"],
        metrics["backend_nudges"],
        avg_silence,
        metrics["silence_durations_s"],
        metrics["false_positives"],
        metrics["turn_completes"],
        metrics["video_frames_in"],
        metrics["audio_chunks_in"],
        metrics["audio_chunks_out"],
    )
