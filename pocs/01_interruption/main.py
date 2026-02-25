"""
POC 01 — Interruption Handling (with client-side VAD)

Minimal FastAPI + WebSocket backend that connects to the Gemini Live API
and focuses exclusively on testing barge-in / interruption behavior.

Two-layer interruption:
  1. Client-side: Silero VAD (via ricky0123/vad-web) detects speech instantly
     in the browser → kills playback in ~50ms, sends barge_in to backend.
  2. Server-side: Gemini's own VAD sends interrupted event → confirmation.

No Firestore, no student profiles, no whiteboard — just audio in/out
with interruption detection, latency tracking, and event logging.

Usage:
    cd pocs/01_interruption
    uvicorn main:app --reload --port 8100
    # Open http://localhost:8100
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
logger = logging.getLogger("poc_interruption")

# ---------------------------------------------------------------------------
# Gemini backend: Vertex AI (same auth as main app)
# ---------------------------------------------------------------------------
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GCP_PROJECT_ID", "seeme-tutor"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("GCP_REGION", "europe-west1"))

from google import genai
from google.genai import types

MODEL = "gemini-live-2.5-flash-native-audio"

# System prompt: intentionally verbose so it's easy to test interruptions
SYSTEM_PROMPT = """\
You are a friendly, talkative tutor named SeeMe. You are currently in a \
test session where we are specifically testing interruption handling.

IMPORTANT BEHAVIORS:
1. When asked a question, give LONG, detailed answers (at least 4-5 sentences). \
This makes it easy for the tester to interrupt you mid-speech.
2. When you are INTERRUPTED, you MUST:
   - Stop speaking IMMEDIATELY — do not finish your sentence
   - Acknowledge the interruption warmly and shortly: "Got it!" or "Sure!" or "Of course!"
   - Wait for the student to speak
   - Then respond to what they said, NOT to what you were saying before
3. If someone says "wait" or "hold on" or "stop" or "just a second" or "just a moment" or "can you wait":
   - Stop immediately
   - Say "Mhm?" or "Uh-huh?", or "Yep?"
   - Wait silently until they speak again
4. If someone changes topic entirely mid-explanation:
   - Do NOT try to go back to the previous topic
   - Follow the new topic naturally

You speak English by default. If someone speaks Portuguese or German, match \
their language.

Start by introducing yourself and asking what the student wants to learn about \
today. Be warm and enthusiastic."""

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="POC 01 — Interruption Handling")

BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)


_TRANSCRIPT_LABELS = {
    "tutor": "Tutor",
    "student": "Student",
    "event": "GEMINI",
    "vad-event": "VAD",
    "error": "ERROR",
}


def _create_session_log(session_id: str):
    """Return a file handle, a write function, and a close function.

    Writes three log files per session:
      - {ts}_{session_id}.jsonl  — raw JSONL (all events with state snapshots)
      - details.log              — human-readable event log, newest-first
      - transcript.log           — conversation transcript, newest-first
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"{ts}_{session_id}.jsonl"
    fh = open(path, "a", buffering=1)  # line-buffered

    # Accumulators; written reversed (newest-first) at session close
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

        # Only client-forwarded events (from logEvent / addTranscript) carry "text"
        text = extra.get("text", "")
        if source != "client" or not text:
            return

        if event.startswith("transcript_"):
            tr_type = event[len("transcript_"):]
            label = _TRANSCRIPT_LABELS.get(tr_type, tr_type.upper())
            ts_short = now.strftime("%H:%M:%S")
            transcript_lines.append(f"{ts_short} {label}: {text}")
        else:
            # All other client events → details log
            ms = f"{now.microsecond // 1000:03d}"
            ts_detail = now.strftime("%H:%M:%S.") + ms
            details_lines.append(f"[{ts_detail}] {text}")

    def close_logs():
        fh.close()
        # Write newest-first (reverse insertion order)
        (LOGS_DIR / "details.log").write_text(
            "\n".join(reversed(details_lines)) + ("\n" if details_lines else "")
        )
        (LOGS_DIR / "transcript.log").write_text(
            "\n".join(reversed(transcript_lines)) + ("\n" if transcript_lines else "")
        )

    logger.info("Session log: %s", path)
    return fh, write, close_logs


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = BASE_DIR / "index.html"
    return HTMLResponse(index_path.read_text())


@app.get("/health")
async def health():
    return {"status": "ok", "poc": "01_interruption"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"poc-{int(time.time())}"
    logger.info("Session %s: WebSocket connected", session_id)

    # Metrics
    metrics = {
        # Gemini server-side interruptions
        "gemini_interruptions": 0,
        "gemini_latencies_ms": [],
        # Client-side VAD barge-ins
        "vad_bargeins": 0,
        "vad_latencies_ms": [],
        # General
        "turn_completes": 0,
        "audio_chunks_in": 0,
        "audio_chunks_out": 0,
        "last_audio_in_at": 0.0,
        "last_audio_out_at": 0.0,
        "assistant_speaking": False,
        "speaking_started_at": 0.0,
        # For comparing VAD vs Gemini: when VAD fires, record time so we can
        # measure how much later Gemini's interrupted arrives.
        "last_vad_bargein_at": 0.0,
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
                    # LOW sensitivity: client-side VAD gates audio, so Gemini only
                    # receives real speech. No need for aggressive server-side detection.
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

            done, pending = await asyncio.wait(
                {forward_task, receive_task},
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
             vad_bargeins=metrics["vad_bargeins"],
             gemini_interruptions=metrics["gemini_interruptions"],
             turn_completes=metrics["turn_completes"],
             audio_in=metrics["audio_chunks_in"],
             audio_out=metrics["audio_chunks_out"])
        close_logs()


def _log_final_metrics(session_id: str, metrics: dict):
    def _avg(lst):
        return sum(lst) / len(lst) if lst else 0

    vad_avg = _avg(metrics["vad_latencies_ms"])
    gemini_avg = _avg(metrics["gemini_latencies_ms"])
    improvement = gemini_avg - vad_avg if gemini_avg > 0 and vad_avg > 0 else 0

    logger.info(
        "Session %s FINAL METRICS:\n"
        "  VAD barge-ins=%d  avg_latency=%.0fms  all=%s\n"
        "  Gemini interrupts=%d  avg_latency=%.0fms  all=%s\n"
        "  VAD advantage=%.0fms  turns=%d  audio_in=%d  audio_out=%d",
        session_id,
        metrics["vad_bargeins"], vad_avg, metrics["vad_latencies_ms"],
        metrics["gemini_interruptions"], gemini_avg, metrics["gemini_latencies_ms"],
        improvement,
        metrics["turn_completes"],
        metrics["audio_chunks_in"],
        metrics["audio_chunks_out"],
    )


async def _forward_browser_to_gemini(
    websocket: WebSocket,
    session,
    session_id: str,
    metrics: dict,
    slog,
):
    """Receive audio from the browser and forward to Gemini."""
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = message.get("type")

            if msg_type == "audio":
                encoded = message.get("data")
                if not encoded:
                    continue
                try:
                    audio_bytes = base64.b64decode(encoded)
                except binascii.Error:
                    continue

                now = time.time()
                metrics["audio_chunks_in"] += 1
                metrics["last_audio_in_at"] = now

                await session.send_realtime_input(
                    audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                )

            elif msg_type == "barge_in":
                now = time.time()
                metrics["vad_bargeins"] += 1
                metrics["last_vad_bargein_at"] = now

                spoke_for_ms = 0.0
                if metrics["speaking_started_at"] > 0:
                    spoke_for_ms = (now - metrics["speaking_started_at"]) * 1000

                client_latency_ms = message.get("client_latency_ms", 0)
                metrics["vad_latencies_ms"].append(client_latency_ms)

                logger.info(
                    "VAD BARGE-IN #%d — client_latency=%dms, tutor_spoke_for=%.0fms",
                    metrics["vad_bargeins"],
                    client_latency_ms,
                    spoke_for_ms,
                )
                slog("client", "vad_bargein",
                     count=metrics["vad_bargeins"],
                     client_latency_ms=client_latency_ms,
                     spoke_for_ms=round(spoke_for_ms))

            elif msg_type == "client_log":
                # Frontend event log entries forwarded for file logging
                slog("client", message.get("event", "log"),
                     text=message.get("text", ""),
                     **{k: v for k, v in message.items()
                        if k not in ("type", "event", "text")})

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

                tool_call = getattr(msg, "tool_call", None)
                if tool_call is not None:
                    continue

                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                # --- Interruption (Gemini server-side) ---
                if getattr(server_content, "interrupted", False):
                    now = time.time()
                    # Ignore stale interrupt notifications that arrive after the
                    # assistant already finished speaking.
                    if not metrics["assistant_speaking"]:
                        stale_lat_ms = 0.0
                        if metrics["last_audio_in_at"] > 0:
                            stale_lat_ms = (now - metrics["last_audio_in_at"]) * 1000
                        metrics["last_vad_bargein_at"] = 0.0
                        logger.info(
                            "IGNORED GEMINI INTERRUPT — assistant not speaking, gemini_lat=%.0fms",
                            stale_lat_ms,
                        )
                        slog("server", "gemini_interrupt_ignored",
                             reason="assistant_not_speaking",
                             stale_lat_ms=round(stale_lat_ms))
                        continue

                    metrics["gemini_interruptions"] += 1
                    metrics["assistant_speaking"] = False

                    # Latency: time from last audio chunk we received from student
                    gemini_lat_ms = 0.0
                    if metrics["last_audio_in_at"] > 0:
                        gemini_lat_ms = (now - metrics["last_audio_in_at"]) * 1000
                    metrics["gemini_latencies_ms"].append(gemini_lat_ms)

                    # How long after the client VAD barge-in did Gemini confirm?
                    vad_to_gemini_ms = 0.0
                    if metrics["last_vad_bargein_at"] > 0:
                        vad_to_gemini_ms = (now - metrics["last_vad_bargein_at"]) * 1000
                        metrics["last_vad_bargein_at"] = 0.0  # reset

                    speaking_duration_ms = 0.0
                    if metrics["speaking_started_at"] > 0:
                        speaking_duration_ms = (now - metrics["speaking_started_at"]) * 1000
                    metrics["speaking_started_at"] = 0.0

                    logger.info(
                        "GEMINI INTERRUPTED #%d — gemini_lat=%.0fms, vad_to_gemini=%.0fms, spoke_for=%.0fms",
                        metrics["gemini_interruptions"],
                        gemini_lat_ms,
                        vad_to_gemini_ms,
                        speaking_duration_ms,
                    )
                    slog("server", "gemini_interrupted",
                         count=metrics["gemini_interruptions"],
                         gemini_lat_ms=round(gemini_lat_ms),
                         vad_to_gemini_ms=round(vad_to_gemini_ms),
                         spoke_for_ms=round(speaking_duration_ms))

                    await websocket.send_text(json.dumps({
                        "type": "interrupted",
                        "data": {
                            "source": "gemini",
                            "count": metrics["gemini_interruptions"],
                            "latency_ms": round(gemini_lat_ms),
                            "vad_to_gemini_ms": round(vad_to_gemini_ms),
                            "spoke_for_ms": round(speaking_duration_ms),
                        },
                    }))
                    continue

                # --- Turn complete ---
                turn_complete = getattr(server_content, "turn_complete", False)

                # --- Audio / text content ---
                model_turn = getattr(server_content, "model_turn", None)
                if model_turn is not None:
                    parts = getattr(model_turn, "parts", None) or []
                    for part in parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data is not None and inline_data.data:
                            now = time.time()
                            if not metrics["assistant_speaking"]:
                                metrics["assistant_speaking"] = True
                                metrics["speaking_started_at"] = now
                            metrics["audio_chunks_out"] += 1
                            metrics["last_audio_out_at"] = now

                            encoded = base64.b64encode(inline_data.data).decode("utf-8")
                            await websocket.send_text(json.dumps({
                                "type": "audio",
                                "data": encoded,
                            }))

                        text = getattr(part, "text", None)
                        if text:
                            logger.info("TUTOR: %s", text)
                            slog("server", "tutor_text", text=text)
                            await websocket.send_text(json.dumps({
                                "type": "text",
                                "data": text,
                            }))

                # --- Input transcription ---
                input_transcription = getattr(server_content, "input_transcription", None)
                if input_transcription is not None:
                    transcript_text = getattr(input_transcription, "text", None)
                    if transcript_text:
                        logger.info("STUDENT: %s", transcript_text)
                        slog("server", "student_transcript", text=transcript_text)
                        await websocket.send_text(json.dumps({
                            "type": "input_transcript",
                            "data": transcript_text,
                        }))

                # --- Output transcription ---
                output_transcription = getattr(server_content, "output_transcription", None)
                if output_transcription is not None:
                    transcript_text = getattr(output_transcription, "text", None)
                    if transcript_text:
                        await websocket.send_text(json.dumps({
                            "type": "output_transcript",
                            "data": transcript_text,
                        }))

                if turn_complete:
                    metrics["turn_completes"] += 1
                    metrics["assistant_speaking"] = False
                    metrics["speaking_started_at"] = 0.0
                    metrics["last_vad_bargein_at"] = 0.0

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
