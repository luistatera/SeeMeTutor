"""
POC 06 — Session Resilience

Minimal FastAPI + WebSocket backend that connects to the Gemini Live API
and tests the app's ability to survive network drops, WebSocket failures,
and Gemini API disconnects — auto-reconnecting and resuming the session
without losing context.

Key behaviors tested:
  - Auto-reconnect: Gemini session re-established within <=2 seconds
  - State preservation: student name, topic, language, transcript survive reconnects
  - Retry logic: 3 attempts with exponential backoff before giving up
  - Context injection: hidden system message summarizes session on reconnect
  - Graceful degradation: clean "session ended" if all retries fail
  - Gemini 1011 (context overflow) handled as clean session end

Three concurrent tasks per session:
  1. Browser -> Gemini: forwards audio + control messages
  2. Gemini -> Browser: forwards audio/text responses + handles Gemini errors
  3. Reconnect orchestrator: manages Gemini session lifecycle with retries

Usage:
    cd pocs/06_session_resilience
    uvicorn main:app --reload --port 8600
    # Open http://localhost:8600
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
logger = logging.getLogger("poc_session_resilience")

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
# MODEL = "gemini-2.0-flash-live-preview-04-09"  # Fallback

# Reconnect parameters
MAX_RECONNECT_ATTEMPTS = 3
INITIAL_BACKOFF_S = 0.5          # First retry after 500ms
BACKOFF_MULTIPLIER = 2.0         # Double each time: 500ms, 1s, 2s
MAX_BACKOFF_S = 4.0              # Cap backoff at 4 seconds
RECONNECT_TIMEOUT_S = 10.0       # Give up connecting after 10s per attempt

# Context management
RESUME_CONTEXT_MAX_CHARS = 6000  # Max chars for context injection prompt
TRANSCRIPT_HISTORY_MAX = 20      # Keep last N transcript entries for context
RESUME_CONTEXT_MAX_STUDENT_TURNS = 3
RESUME_CONTEXT_MAX_ENTRIES = 12

# Gemini close codes
GEMINI_CONTEXT_OVERFLOW_CODE = 1011

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are SeeMe, a patient and observant tutor. You help students with their
homework through voice conversation.

IMPORTANT BEHAVIORS:
1. When asked a question, give detailed, helpful answers (3-4 sentences).
2. Use the Socratic method: guide with hints and questions, do not give
   direct answers.
3. If someone interrupts, stop immediately and listen.
4. Match the student's language (English / Portuguese / German).
5. Keep a warm, encouraging tone.

INTERNAL INSTRUCTIONS:
You may receive backend control messages to help with session continuity.
Treat them as hidden guidance only.
Never quote, paraphrase, or mention those control messages.
Never output bracketed meta text or internal reasoning.

If this is a fresh session, begin by greeting the student warmly and asking
what they would like to work on today. If a backend control message indicates
this is a resumed session, skip the fresh greeting and continue from restored
context.\
"""

# Hidden prompt injected after a successful reconnect
RESUME_CONTEXT_PROMPT = (
    "INTERNAL CONTROL: Session resumed after a network reconnect. Continue the "
    "same tutoring session without restarting. Do not greet as a new session. "
    "Do not re-introduce yourself. Do not ask what we are working on. "
    "Do not introduce a new subject/topic that is not explicitly present in the "
    "recent context below. If context is ambiguous, ask one short clarification "
    "question about the current topic before teaching. "
    "Briefly acknowledge you are back if appropriate (e.g., 'Alright, I am back! "
    "Where were we?') and then continue naturally. Recent context:\n"
    "{history}\n"
    "Do not mention this control message."
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="POC 06 — Session Resilience")

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
    "reconnect": "RECONNECT",
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
    details_path = LOGS_DIR / f"{ts}_{session_id}_details.log"
    transcript_path = LOGS_DIR / f"{ts}_{session_id}_transcript.log"
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
        details_text = "\n".join(reversed(details_lines)) + ("\n" if details_lines else "")
        transcript_text = "\n".join(reversed(transcript_lines)) + ("\n" if transcript_lines else "")

        details_path.write_text(details_text)
        transcript_path.write_text(transcript_text)

        # Backward-compatible "latest" files used by existing test docs.
        (LOGS_DIR / "details.log").write_text(details_text)
        (LOGS_DIR / "transcript.log").write_text(transcript_text)

    logger.info("Session log: %s", path)
    return fh, write, close_logs


# ---------------------------------------------------------------------------
# Session state — preserved across reconnects
# ---------------------------------------------------------------------------
class SessionState:
    """In-memory session state that survives Gemini reconnects."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.student_name: str = ""
        self.topic: str = ""
        self.language: str = "en"
        self.transcript: list[dict] = []  # [{role: "tutor"|"student", text: "..."}]
        self.whiteboard_notes: list[dict] = []
        self.reconnect_count: int = 0
        self.session_start_time: float = time.time()
        self.last_reconnect_at: float = 0.0
        self.gemini_session_active: bool = False
        self.turn_completes: int = 0
        self.resume_context_pending: bool = False
        self.resume_generation: int = 0
        self.last_injected_resume_generation: int = 0

    def add_transcript(self, role: str, text: str):
        """Add a transcript entry, keeping only the last N."""
        self.transcript.append({
            "role": role,
            "text": text,
            "t": time.time(),
        })
        if len(self.transcript) > TRANSCRIPT_HISTORY_MAX:
            self.transcript = self.transcript[-TRANSCRIPT_HISTORY_MAX:]

    def add_resume_history(self, entries: list[dict]) -> int:
        """Merge client resume history using a strict recent-window policy."""
        if not isinstance(entries, list) or not entries:
            return 0

        normalized: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role", "student")
            if role not in ("student", "tutor"):
                role = "student"
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            normalized.append({"role": role, "text": text})

        if not normalized:
            return 0

        # Keep only the segment covering the most recent N student turns.
        student_seen = 0
        start_idx = 0
        for i in range(len(normalized) - 1, -1, -1):
            if normalized[i]["role"] == "student":
                student_seen += 1
                if student_seen >= RESUME_CONTEXT_MAX_STUDENT_TURNS:
                    start_idx = i
                    break

        # Never resume from tutor-only fragments; require at least one student turn.
        if student_seen == 0:
            return 0

        clipped = normalized[start_idx:]
        if len(clipped) > RESUME_CONTEXT_MAX_ENTRIES:
            clipped = clipped[-RESUME_CONTEXT_MAX_ENTRIES:]

        for entry in clipped:
            self.add_transcript(entry["role"], entry["text"])
        return len(clipped)

    def add_whiteboard_note(self, note: dict):
        if not isinstance(note, dict):
            return
        title = str(note.get("title", "")).strip()
        content = str(note.get("content", "")).strip()
        if not title and not content:
            return
        self.whiteboard_notes.append({
            "title": title,
            "content": content[:600],
            "t": time.time(),
        })
        if len(self.whiteboard_notes) > 10:
            self.whiteboard_notes = self.whiteboard_notes[-10:]

    def apply_session_state_payload(self, payload: dict):
        if not isinstance(payload, dict):
            return
        if payload.get("student_name"):
            self.student_name = str(payload["student_name"]).strip()
        if payload.get("topic"):
            self.topic = str(payload["topic"]).strip()
        if payload.get("language"):
            self.language = str(payload["language"]).strip().lower()

        notes = payload.get("whiteboard_notes")
        if isinstance(notes, list):
            for note in notes:
                self.add_whiteboard_note(note)

    def build_resume_context(self) -> str:
        """Build the context injection prompt for Gemini after reconnect."""
        parts = []
        if self.student_name:
            parts.append(f"Student name: {self.student_name}")
        if self.topic:
            parts.append(f"Topic: {self.topic}")
        if self.language and self.language != "en":
            parts.append(f"Language: {self.language}")

        if self.transcript:
            parts.append("Recent conversation:")
            for entry in self.transcript[-10:]:  # last 10 turns
                role_label = "Student" if entry["role"] == "student" else "Tutor"
                text = entry["text"][:300]  # truncate long entries
                parts.append(f"  {role_label}: {text}")

        if self.whiteboard_notes:
            parts.append("Recent whiteboard notes:")
            for note in self.whiteboard_notes[-3:]:
                title = note.get("title") or "Untitled"
                content = note.get("content") or ""
                parts.append(f"  - {title}: {content[:240]}")

        history = "\n".join(parts)
        if len(history) > RESUME_CONTEXT_MAX_CHARS:
            history = history[:RESUME_CONTEXT_MAX_CHARS]

        return RESUME_CONTEXT_PROMPT.format(history=history)

    def to_dict(self) -> dict:
        """Serialize state for frontend."""
        return {
            "session_id": self.session_id,
            "student_name": self.student_name,
            "topic": self.topic,
            "language": self.language,
            "reconnect_count": self.reconnect_count,
            "turn_completes": self.turn_completes,
            "transcript_count": len(self.transcript),
            "whiteboard_count": len(self.whiteboard_notes),
            "resume_generation": self.resume_generation,
            "uptime_s": round(time.time() - self.session_start_time, 1),
        }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return HTMLResponse((BASE_DIR / "index.html").read_text())


@app.get("/health")
async def health():
    return {"status": "ok", "poc": "06_session_resilience"}


# ---------------------------------------------------------------------------
# Helper: send hidden turn to Gemini
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


async def _inject_resume_context(
    session,
    state: SessionState,
    metrics: dict,
    slog,
    *,
    source: str,
    force: bool = False,
) -> bool:
    """Inject hidden continuity context into Gemini.

    Returns True when context was sent, False when skipped or failed.
    """
    should_inject = force or state.resume_context_pending
    if not should_inject:
        return False

    # Avoid duplicate injection for the same browser resume payload generation.
    if (
        not force
        and state.resume_generation > 0
        and state.last_injected_resume_generation >= state.resume_generation
    ):
        return False

    context_prompt = state.build_resume_context()
    try:
        await _send_hidden_turn(session, context_prompt)
        metrics["context_injections"] += 1
        state.last_injected_resume_generation = state.resume_generation
        state.resume_context_pending = False
        slog(
            "server",
            "context_injected",
            chars=len(context_prompt),
            count=metrics["context_injections"],
            inject_source=source,
            force=force,
        )
        return True
    except Exception as exc:
        slog("server", "context_injection_failed", error=str(exc), inject_source=source)
        return False


# ---------------------------------------------------------------------------
# Gemini session creation with config
# ---------------------------------------------------------------------------
def _build_gemini_config() -> types.LiveConnectConfig:
    """Build the Gemini Live API connection config."""
    return types.LiveConnectConfig(
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


# ---------------------------------------------------------------------------
# WebSocket endpoint — main session orchestrator
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = f"poc06-{int(time.time())}"
    logger.info("Session %s: WebSocket connected", session_id)

    state = SessionState(session_id)
    log_fh, slog, close_logs = _create_session_log(session_id)

    # Metrics for this session
    metrics = {
        "reconnect_attempts": 0,
        "reconnect_successes": 0,
        "reconnect_failures": 0,
        "gemini_errors": 0,
        "context_injections": 0,
        "turn_completes": 0,
        "audio_chunks_in": 0,
        "audio_chunks_out": 0,
        "assistant_speaking": False,
        "speaking_started_at": 0.0,
        "last_audio_out_at": 0.0,
    }

    # Shared mutable state for the Gemini session (replaced on reconnect)
    gemini_holder = {
        "session": None,
        "connected": False,
        "shutting_down": False,
        "last_receive_error": None,
    }

    # Queue for audio from browser that needs to be forwarded to Gemini
    audio_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    # Event signals
    gemini_ready_event = asyncio.Event()
    shutdown_event = asyncio.Event()

    try:
        slog("server", "session_start")

        # Start the three main tasks
        browser_task = asyncio.create_task(
            _receive_from_browser(
                websocket, audio_queue, state, metrics, slog,
                gemini_holder, gemini_ready_event, shutdown_event,
            ),
            name="browser_receiver",
        )
        gemini_lifecycle_task = asyncio.create_task(
            _gemini_session_lifecycle(
                websocket, audio_queue, state, metrics, slog,
                gemini_holder, gemini_ready_event, shutdown_event,
            ),
            name="gemini_lifecycle",
        )

        done, pending = await asyncio.wait(
            {browser_task, gemini_lifecycle_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        shutdown_event.set()
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except Exception as exc:
        logger.exception("Session %s: top-level error: %s", session_id, exc)
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "data": str(exc),
            }))
        except Exception:
            pass

    finally:
        _log_final_metrics(session_id, metrics, state)
        slog("server", "session_end",
             reconnect_attempts=metrics["reconnect_attempts"],
             reconnect_successes=metrics["reconnect_successes"],
             reconnect_failures=metrics["reconnect_failures"],
             gemini_errors=metrics["gemini_errors"],
             context_injections=metrics["context_injections"],
             turns=metrics["turn_completes"],
             audio_in=metrics["audio_chunks_in"],
             audio_out=metrics["audio_chunks_out"])
        close_logs()


# ---------------------------------------------------------------------------
# Browser -> Backend: receive audio + control messages
# ---------------------------------------------------------------------------
async def _receive_from_browser(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    state: SessionState,
    metrics: dict,
    slog,
    gemini_holder: dict,
    gemini_ready_event: asyncio.Event,
    shutdown_event: asyncio.Event,
):
    """Receive messages from the browser and queue audio for Gemini."""
    try:
        while not shutdown_event.is_set():
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

                # Forward directly to Gemini if connected, else drop
                session = gemini_holder.get("session")
                if session and gemini_holder.get("connected"):
                    try:
                        await session.send_realtime_input(
                            audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                        )
                    except Exception:
                        # Gemini session broken — lifecycle manager will handle
                        pass

            # -- Session state update from browser --
            elif msg_type == "session_state":
                data = message.get("data", {})
                state.apply_session_state_payload(data)
                slog("client", "session_state_update",
                     student_name=state.student_name,
                     topic=state.topic,
                     language=state.language,
                     whiteboard_count=len(state.whiteboard_notes))

            # -- Resume context from browser (after browser-side reconnect) --
            elif msg_type == "resume_context":
                # Browser reconnected and is sending its stored transcript
                history = message.get("history", "")
                session_state = message.get("session_state", {})
                whiteboard_notes = message.get("whiteboard_notes", [])
                entries_added = 0
                if isinstance(history, list):
                    entries_added = state.add_resume_history(history)

                if isinstance(whiteboard_notes, list):
                    for note in whiteboard_notes:
                        state.add_whiteboard_note(note)

                state.apply_session_state_payload(session_state)

                if entries_added > 0 or session_state or whiteboard_notes:
                    state.resume_generation += 1
                    state.resume_context_pending = True

                slog(
                    "server",
                    "browser_resume_context_received",
                    history_received=len(history) if isinstance(history, list) else 0,
                    entries=entries_added,
                    whiteboard_count=len(state.whiteboard_notes),
                    resume_generation=state.resume_generation,
                    student_name=state.student_name,
                    topic=state.topic,
                    language=state.language,
                )

                # If Gemini is already connected, inject resume context immediately.
                session = gemini_holder.get("session")
                if session and gemini_holder.get("connected"):
                    injected = await _inject_resume_context(
                        session,
                        state,
                        metrics,
                        slog,
                        source="browser_resume",
                        force=False,
                    )
                    if injected:
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "reconnected",
                                "data": {
                                    "reconnect_count": state.reconnect_count,
                                    "state": state.to_dict(),
                                    "source": "browser_resume",
                                },
                            }))
                        except Exception:
                            return

            # -- Simulate disconnect (test button) --
            elif msg_type == "simulate_disconnect":
                target = message.get("target", "gemini")  # "gemini" | "websocket" | "context_overflow"
                slog("server", "simulate_disconnect", target=target)

                if target == "gemini":
                    # Force-close the Gemini session to trigger reconnect
                    session = gemini_holder.get("session")
                    if session:
                        logger.info("Session %s: SIMULATED Gemini disconnect", state.session_id)
                        gemini_holder["connected"] = False
                        try:
                            await session.close()
                        except Exception:
                            pass
                elif target == "context_overflow":
                    # Simulate Gemini 1011 handling path for deterministic testing.
                    slog("server", "context_overflow_session_end", simulated=True)
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "session_limit",
                            "data": {
                                "reason": "context_overflow_simulated",
                                "message": "Session reached its context limit. Please start a new session.",
                            },
                        }))
                    except Exception:
                        pass
                    shutdown_event.set()
                    return
                elif target == "websocket":
                    # Close the browser WS — browser reconnect logic handles this
                    logger.info("Session %s: SIMULATED WebSocket disconnect", state.session_id)
                    await websocket.close(code=1000, reason="simulated_disconnect")
                    return

            # -- Barge-in (basic interruption support) --
            elif msg_type == "barge_in":
                slog("client", "vad_bargein",
                     client_latency_ms=message.get("client_latency_ms", 0))

            # -- Client-side event logging --
            elif msg_type == "client_log":
                slog("client", message.get("event", "log"),
                     text=message.get("text", ""),
                     **{k: v for k, v in message.items()
                        if k not in ("type", "event", "text")})

            # -- Activity signals (for Gemini's VAD) --
            elif msg_type == "activity_start":
                session = gemini_holder.get("session")
                if session and gemini_holder.get("connected"):
                    try:
                        await session.send_realtime_input(
                            activity_start=types.ActivityStart(),
                        )
                    except Exception:
                        pass
            elif msg_type == "activity_end":
                session = gemini_holder.get("session")
                if session and gemini_holder.get("connected"):
                    try:
                        await session.send_realtime_input(
                            activity_end=types.ActivityEnd(),
                        )
                    except Exception:
                        pass

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected", state.session_id)
    except Exception as exc:
        logger.exception("Session %s: browser receive error: %s", state.session_id, exc)


# ---------------------------------------------------------------------------
# Gemini session lifecycle — connect, run, reconnect on failure
# ---------------------------------------------------------------------------
async def _gemini_session_lifecycle(
    websocket: WebSocket,
    audio_queue: asyncio.Queue,
    state: SessionState,
    metrics: dict,
    slog,
    gemini_holder: dict,
    gemini_ready_event: asyncio.Event,
    shutdown_event: asyncio.Event,
):
    """Manage the Gemini Live API session with auto-reconnect.

    This is the core resilience loop:
    1. Connect to Gemini
    2. Run the forwarding tasks
    3. If Gemini drops, attempt reconnect with exponential backoff
    4. If all retries fail, signal session end to browser
    """
    is_first_connect = True
    reconnect_attempt = 0
    reconnect_reason = "initial"

    while not shutdown_event.is_set():
        # -- Reconnect pacing + attempt accounting --
        if not is_first_connect:
            if reconnect_attempt >= MAX_RECONNECT_ATTEMPTS:
                metrics["reconnect_failures"] += 1
                slog("server", "reconnect_exhausted", attempts=reconnect_attempt)
                try:
                    await websocket.send_text(json.dumps({
                        "type": "session_ended",
                        "data": {
                            "reason": "reconnect_failed",
                            "attempts": reconnect_attempt,
                            "message": "Connection lost. Please restart the session.",
                        },
                    }))
                except Exception:
                    pass
                return

            reconnect_attempt += 1
            metrics["reconnect_attempts"] += 1
            state.reconnect_count += 1
            backoff_s = min(
                INITIAL_BACKOFF_S * (BACKOFF_MULTIPLIER ** (reconnect_attempt - 1)),
                MAX_BACKOFF_S,
            )

            slog(
                "server",
                "reconnect_attempt",
                attempt=reconnect_attempt,
                max_attempts=MAX_RECONNECT_ATTEMPTS,
                backoff_s=round(backoff_s, 1),
                reconnect_count=state.reconnect_count,
                reason=reconnect_reason,
            )

            try:
                await websocket.send_text(json.dumps({
                    "type": "reconnecting",
                    "data": {
                        "attempt": reconnect_attempt,
                        "max_attempts": MAX_RECONNECT_ATTEMPTS,
                        "backoff_s": round(backoff_s, 1),
                        "reason": reconnect_reason,
                    },
                }))
            except Exception:
                return

            await asyncio.sleep(backoff_s)
            if shutdown_event.is_set():
                return

        try:
            client = genai.Client()
            config = _build_gemini_config()
            gemini_holder["last_receive_error"] = None

            slog("server", "gemini_connecting", is_reconnect=not is_first_connect)

            async with client.aio.live.connect(
                model=MODEL,
                config=config,
            ) as session:
                gemini_holder["session"] = session
                gemini_holder["connected"] = True
                gemini_ready_event.set()

                slog(
                    "server",
                    "gemini_connected",
                    is_reconnect=not is_first_connect,
                    reconnect_count=state.reconnect_count,
                )

                # On reconnect OR browser-resume bootstrap, inject hidden context before forwarding.
                injected = await _inject_resume_context(
                    session,
                    state,
                    metrics,
                    slog,
                    source="gemini_reconnect" if not is_first_connect else "browser_resume_bootstrap",
                    force=not is_first_connect,
                )
                if injected:
                    if not is_first_connect:
                        metrics["reconnect_successes"] += 1
                        state.last_reconnect_at = time.time()
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "reconnected",
                            "data": {
                                "reconnect_count": state.reconnect_count,
                                "state": state.to_dict(),
                            },
                        }))
                    except Exception:
                        return
                elif not is_first_connect:
                    metrics["reconnect_successes"] += 1
                    state.last_reconnect_at = time.time()
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "reconnected",
                            "data": {
                                "reconnect_count": state.reconnect_count,
                                "state": state.to_dict(),
                            },
                        }))
                    except Exception:
                        return

                # Successful connect resets retry staircase.
                reconnect_attempt = 0
                is_first_connect = False

                receive_task = asyncio.create_task(
                    _forward_gemini_to_browser(
                        websocket, session, state, metrics, slog,
                        gemini_holder, shutdown_event,
                    ),
                    name="gemini_to_browser",
                )

                shutdown_waiter = asyncio.create_task(shutdown_event.wait())
                done, pending = await asyncio.wait(
                    {receive_task, shutdown_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                if shutdown_event.is_set():
                    return

                gemini_holder["connected"] = False
                gemini_holder["session"] = None
                gemini_ready_event.clear()

                last_receive_error = gemini_holder.get("last_receive_error")
                if last_receive_error and last_receive_error.get("is_context_overflow"):
                    slog("server", "context_overflow_session_end")
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "session_limit",
                            "data": {
                                "reason": "context_overflow",
                                "message": "Session reached its context limit. Please start a new session.",
                            },
                        }))
                    except Exception:
                        pass
                    return

                reconnect_reason = "stream_ended"
                slog("server", "gemini_session_ended", reason=reconnect_reason)

        except Exception as exc:
            gemini_holder["connected"] = False
            gemini_holder["session"] = None
            gemini_ready_event.clear()
            metrics["gemini_errors"] += 1
            is_first_connect = False
            reconnect_reason = "connect_error"

            error_str = str(exc)
            is_context_overflow = "1011" in error_str
            slog(
                "server",
                "gemini_error",
                error=error_str[:500],
                attempt=reconnect_attempt,
                is_context_overflow=is_context_overflow,
            )

            if is_context_overflow:
                slog("server", "context_overflow_session_end")
                try:
                    await websocket.send_text(json.dumps({
                        "type": "session_limit",
                        "data": {
                            "reason": "context_overflow",
                            "message": "Session reached its context limit. Please start a new session.",
                        },
                    }))
                except Exception:
                    pass
                return


# ---------------------------------------------------------------------------
# Gemini -> Browser: audio, text, interruptions, transcriptions
# ---------------------------------------------------------------------------
async def _forward_gemini_to_browser(
    websocket: WebSocket,
    session,
    state: SessionState,
    metrics: dict,
    slog,
    gemini_holder: dict,
    shutdown_event: asyncio.Event,
):
    """Receive responses from Gemini and forward to the browser."""
    turn_index = 0
    gemini_holder["last_receive_error"] = None

    try:
        while not shutdown_event.is_set():
            turn_index += 1
            turn_events = 0

            async for msg in session.receive():
                turn_events += 1

                if shutdown_event.is_set():
                    return

                # Skip tool calls (not used in this POC)
                if getattr(msg, "tool_call", None) is not None:
                    continue

                server_content = getattr(msg, "server_content", None)
                if server_content is None:
                    continue

                # -- Interruption (Gemini server-side) --
                if getattr(server_content, "interrupted", False):
                    if not metrics["assistant_speaking"]:
                        slog("server", "gemini_interrupt_ignored",
                             reason="assistant_not_speaking")
                        continue

                    metrics["assistant_speaking"] = False
                    metrics["speaking_started_at"] = 0.0

                    logger.info("GEMINI INTERRUPTED")
                    slog("server", "gemini_interrupted")

                    try:
                        await websocket.send_text(json.dumps({
                            "type": "interrupted",
                            "data": {"source": "gemini"},
                        }))
                    except Exception:
                        return
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
                            if not metrics["assistant_speaking"]:
                                metrics["assistant_speaking"] = True
                                metrics["speaking_started_at"] = now

                            metrics["audio_chunks_out"] += 1
                            metrics["last_audio_out_at"] = now

                            encoded = base64.b64encode(inline_data.data).decode("utf-8")
                            try:
                                await websocket.send_text(json.dumps({
                                    "type": "audio",
                                    "data": encoded,
                                }))
                            except Exception:
                                return

                        # Text output
                        text = getattr(part, "text", None)
                        if text:
                            logger.info("TUTOR: %s", text)
                            slog("server", "tutor_text", text=text)
                            state.add_transcript("tutor", text)
                            try:
                                await websocket.send_text(json.dumps({
                                    "type": "text",
                                    "data": text,
                                }))
                            except Exception:
                                return

                # -- Input transcription (student speech) --
                input_transcription = getattr(server_content, "input_transcription", None)
                if input_transcription is not None:
                    transcript_text = getattr(input_transcription, "text", None)
                    if transcript_text:
                        logger.info("STUDENT: %s", transcript_text)
                        slog("server", "student_transcript", text=transcript_text)
                        state.add_transcript("student", transcript_text)
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "input_transcript",
                                "data": transcript_text,
                            }))
                        except Exception:
                            return

                # -- Output transcription (tutor speech) --
                output_transcription = getattr(server_content, "output_transcription", None)
                if output_transcription is not None:
                    transcript_text = getattr(output_transcription, "text", None)
                    if transcript_text:
                        state.add_transcript("tutor", transcript_text)
                        try:
                            await websocket.send_text(json.dumps({
                                "type": "output_transcript",
                                "data": transcript_text,
                            }))
                        except Exception:
                            return

                # -- Turn complete --
                if turn_complete:
                    metrics["turn_completes"] += 1
                    state.turn_completes += 1
                    metrics["assistant_speaking"] = False
                    metrics["speaking_started_at"] = 0.0

                    logger.info("TURN COMPLETE #%d", metrics["turn_completes"])
                    slog("server", "turn_complete", count=metrics["turn_completes"])
                    try:
                        await websocket.send_text(json.dumps({
                            "type": "turn_complete",
                            "data": {"count": metrics["turn_completes"]},
                        }))
                    except Exception:
                        return

            if turn_events == 0:
                logger.info("Session %s: Gemini stream ended", state.session_id)
                return
            await asyncio.sleep(0)

    except WebSocketDisconnect:
        logger.info("Session %s: browser disconnected (gemini receiver)", state.session_id)
    except Exception as exc:
        error_str = str(exc)
        is_context_overflow = "1011" in error_str
        logger.warning(
            "Session %s: Gemini receive error: %s (context_overflow=%s)",
            state.session_id, exc, is_context_overflow,
        )
        slog("server", "gemini_receive_error",
             error=error_str[:500],
             is_context_overflow=is_context_overflow)
        gemini_holder["last_receive_error"] = {
            "error": error_str[:500],
            "is_context_overflow": is_context_overflow,
        }
        # Return and let lifecycle manager handle reconnect
        return


# ---------------------------------------------------------------------------
# Final metrics summary
# ---------------------------------------------------------------------------
def _log_final_metrics(session_id: str, metrics: dict, state: SessionState):
    logger.info(
        "Session %s FINAL METRICS:\n"
        "  Reconnect attempts=%d  successes=%d  failures=%d\n"
        "  Gemini errors=%d  context_injections=%d\n"
        "  Turns=%d  audio_in=%d  audio_out=%d\n"
        "  Session duration=%.0fs  total_reconnects=%d",
        session_id,
        metrics["reconnect_attempts"],
        metrics["reconnect_successes"],
        metrics["reconnect_failures"],
        metrics["gemini_errors"],
        metrics["context_injections"],
        metrics["turn_completes"],
        metrics["audio_chunks_in"],
        metrics["audio_chunks_out"],
        time.time() - state.session_start_time,
        state.reconnect_count,
    )
