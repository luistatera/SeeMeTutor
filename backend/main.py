"""
SeeMe Tutor — FastAPI backend.

Bridges browser WebSocket connections to the Gemini Live API.
Audio and video frames flow from the browser to Gemini; audio responses
and text transcripts flow back to the browser.
"""

import asyncio
import base64
import binascii
import json
import logging
import os
import time
import uuid
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from google.adk import Runner
from google.adk.sessions import InMemorySessionService

from gemini_live import ADKLiveSession, APP_NAME
from tutor_agent.agent import root_agent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    os.environ.setdefault("GOOGLE_API_KEY", GEMINI_API_KEY)
else:
    logger.warning(
        "GEMINI_API_KEY is not set. WebSocket connections will fail. "
        "Set the variable in .env or as an environment variable."
    )

SESSION_TIMEOUT_SECONDS = 20 * 60  # 20-minute focused session limit

# Firestore client for session logging (optional — works without it for local dev)
firestore_client = None
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "seeme-tutor")
try:
    from google.cloud import firestore as firestore_module
    firestore_client = firestore_module.AsyncClient(project=GCP_PROJECT_ID)
    logger.info("Firestore client initialized (project=%s)", GCP_PROJECT_ID)
except ImportError:
    logger.info("google-cloud-firestore not installed — session logging disabled (OK for local dev)")
except Exception:
    logger.error(
        "Firestore client failed to initialize for project=%s — session logging disabled. "
        "Check service account credentials and IAM permissions.",
        GCP_PROJECT_ID,
        exc_info=True,
    )
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = FastAPI(
    title="SeeMe Tutor",
    description="Real-time multimodal AI tutoring via Gemini Live API.",
    version="1.0.0",
)

_session_service = InMemorySessionService()
_runner = Runner(
    app_name=APP_NAME,
    agent=root_agent,
    session_service=_session_service,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")
    logger.info("Serving frontend static files from %s", FRONTEND_DIR)
else:
    logger.warning("Frontend directory not found at %s — static serving disabled", FRONTEND_DIR)


@app.get("/", include_in_schema=False)
async def serve_index() -> FileResponse:
    """Serve the frontend single-page application."""
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_path))


@app.get("/health")
async def health_check() -> dict:
    """Liveness probe for Cloud Run."""
    return {"status": "ok", "service": "seeme-tutor"}


class _StudentEndedSession(Exception):
    """Raised by _forward_to_gemini when the student explicitly ends the session."""


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    Full-duplex WebSocket bridge between the browser and Gemini Live API.

    Browser → server message format (JSON):
        {"type": "audio", "data": "<base64-encoded PCM 16-bit 16 kHz>"}
        {"type": "video", "data": "<base64-encoded JPEG>"}

    Server → browser message format (JSON):
        {"type": "audio", "data": "<base64-encoded PCM 16-bit 24 kHz>"}
        {"type": "text", "data": "<transcript segment>"}
        {"type": "turn_complete"}
        {"type": "interrupted"}
        {"type": "session_limit"}
        {"type": "error", "data": "<description>"}
    """
    await websocket.accept()
    client_host = websocket.client.host if websocket.client else "unknown"
    session_id = str(uuid.uuid4())
    session_start = time.time()
    logger.info("Session %s accepted from %s", session_id, client_host)

    if not GEMINI_API_KEY:
        await _send_json(websocket, {"type": "error", "data": "Server misconfiguration: API key not set."})
        await websocket.close()
        return

    # Log session start to Firestore
    if firestore_client:
        try:
            await firestore_client.collection("sessions").document(session_id).set({
                "started_at": session_start,
                "client_host": client_host,
                "ended_reason": None,
                "duration_seconds": None,
            })
        except Exception:
            logger.warning("Session %s: failed to log start to Firestore", session_id, exc_info=True)

    # Create ADK session with state accessible to tools (e.g. log_progress)
    await _session_service.create_session(
        app_name=APP_NAME,
        user_id="browser",
        session_id=session_id,
        state={"session_id": session_id, "gcp_project_id": GCP_PROJECT_ID},
    )

    ended_reason = "disconnect"
    try:
        try:
            async with ADKLiveSession(
                runner=_runner,
                user_id="browser",
                session_id=session_id,
            ) as gemini_session:
                forward_task = asyncio.create_task(
                    _forward_to_gemini(websocket, gemini_session),
                    name="forward_to_gemini",
                )
                receive_task = asyncio.create_task(
                    _forward_to_client(websocket, gemini_session),
                    name="forward_to_client",
                )
                timer_task = asyncio.create_task(
                    _session_timer(websocket, SESSION_TIMEOUT_SECONDS),
                    name="session_timer",
                )

                done, pending = await asyncio.wait(
                    {forward_task, receive_task, timer_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.exception("Error while cancelling task %s", task.get_name())

                for task in done:
                    try:
                        exc = task.exception()
                    except asyncio.CancelledError:
                        logger.debug("Task %s was cancelled", task.get_name())
                        continue
                    if task is timer_task and exc is None:
                        ended_reason = "limit"
                    elif isinstance(exc, _StudentEndedSession):
                        ended_reason = "student_ended"
                    elif exc is not None:
                        logger.error("Task %s raised: %s", task.get_name(), exc, exc_info=exc)

        except Exception as exc:
            logger.exception("Session %s: Gemini session error: %s", session_id, exc)
            await _send_json(websocket, {
                "type": "error",
                "data": "Could not connect to the tutoring service. Please try again in a moment.",
            })
            ended_reason = "gemini_error"

    finally:
        duration = int(time.time() - session_start)
        if firestore_client:
            try:
                await firestore_client.collection("sessions").document(session_id).update({
                    "ended_reason": ended_reason,
                    "duration_seconds": duration,
                })
            except Exception:
                logger.warning("Session %s: failed to log end to Firestore", session_id, exc_info=True)
        logger.info("Session %s ended after %ds (reason: %s)", session_id, duration, ended_reason)


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


async def _forward_to_gemini(websocket: WebSocket, session: ADKLiveSession) -> None:
    """
    Receive JSON messages from the browser and forward media to Gemini.

    Runs until the WebSocket is disconnected or an unrecoverable error occurs.
    """
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

            # Control messages (no data payload)
            if msg_type == "end_session":
                logger.info("Student requested end_session")
                raise _StudentEndedSession()
            if msg_type in ("mic_stop", "camera_off"):
                logger.info("Control message from browser: '%s'", msg_type)
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
                session.send_audio(raw_bytes)
            elif msg_type == "video":
                session.send_video_frame(raw_bytes)
            else:
                logger.warning("Unknown message type from browser: '%s'", msg_type)

    except WebSocketDisconnect:
        logger.info("Browser disconnected (forward_to_gemini)")
    except _StudentEndedSession:
        raise
    except Exception as exc:
        logger.exception("Unexpected error in forward_to_gemini: %s", exc)
        await _send_json(websocket, {
            "type": "error",
            "data": "The connection to the tutor was interrupted. Please refresh to start a new session.",
        })


async def _forward_to_client(websocket: WebSocket, session: ADKLiveSession) -> None:
    """
    Receive responses from Gemini and forward them to the browser.

    Runs until the Gemini session closes, the WebSocket disconnects,
    or an unrecoverable error occurs.
    """
    try:
        async for event in session.receive():
            event_type = event.get("type")

            if event_type == "audio":
                audio_bytes: bytes = event["data"]
                encoded = base64.b64encode(audio_bytes).decode("utf-8")
                await _send_json(websocket, {"type": "audio", "data": encoded})

            elif event_type == "text":
                await _send_json(websocket, {"type": "text", "data": event["data"]})

            elif event_type == "turn_complete":
                await _send_json(websocket, {"type": "turn_complete"})
                logger.debug("Turn complete signal sent to browser")

            elif event_type == "interrupted":
                await _send_json(websocket, {"type": "interrupted"})
                logger.debug("Interrupted signal sent to browser")

            else:
                logger.warning("Unknown event type from Gemini session: '%s'", event_type)

    except WebSocketDisconnect:
        logger.info("Browser disconnected (forward_to_client)")
    except Exception as exc:
        logger.exception("Unexpected error in forward_to_client: %s", exc)
        await _send_json(websocket, {
            "type": "error",
            "data": "The tutor connection was interrupted. Please refresh to start a new session.",
        })


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
