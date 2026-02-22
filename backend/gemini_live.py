"""
ADK Live session management for SeeMe Tutor.

Wraps the ADK Runner's live streaming mode, translating ADK Events into the
same dict format that main.py already consumes. The frontend WebSocket
protocol does not change at all.
"""

import asyncio
import logging
import time
from types import TracebackType
from typing import AsyncGenerator, Optional

from google.adk import Runner
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import RunConfig
from google.adk.flows.llm_flows.base_llm_flow import StreamingMode
from google.genai import types

# --- MONKEY PATCH FOR ADK APIError 1007 ---
import google.adk.models.gemini_llm_connection

_original_send_content = google.adk.models.gemini_llm_connection.GeminiLlmConnection.send_content

async def _patched_send_content(self, content: types.Content):
    """
    Avoid sending turn_complete=True to prevent APIError 1007 when VAD is active.
    If the content is a function response, let it behave normally.
    """
    assert content.parts
    if content.parts[0].function_response:
        await _original_send_content(self, content)
    else:
        # For regular text content, omit turn_complete to avoid conflicting with VAD
        # The ADK logger uses 'google_adk.google.adk.models.gemini_llm_connection'
        import logging
        logging.getLogger('google_adk.google.adk.models.gemini_llm_connection').debug('Sending LLM new content (patched) %s', content)
        
        # Omit turn_complete=True
        await self._gemini_session.send(
            input=types.LiveClientContent(
                turns=[content],
            )
        )

# Apply patch
google.adk.models.gemini_llm_connection.GeminiLlmConnection.send_content = _patched_send_content
# ------------------------------------------

# --- MONKEY PATCH FOR WEBSOCKET KEEPALIVE TIMEOUT (1011 Error) ---
# The Gemini Live API can sometimes take longer than the default 20s to respond to pings
# causing a 1011 ConnectionClosedError. We intercept the websockets connect call and
# override ping_interval and ping_timeout for generativelanguage endpoints.
try:
    import websockets.asyncio.client
    _original_ws_connect = websockets.asyncio.client.connect

    def _patched_ws_connect(uri, **kwargs):
        if "generativelanguage" in uri:
            kwargs.setdefault("ping_interval", 120)
            kwargs.setdefault("ping_timeout", 120)
        return _original_ws_connect(uri, **kwargs)

    websockets.asyncio.client.connect = _patched_ws_connect
    # If using newer websockets, google.genai aliases it
    import google.genai.live
    if hasattr(google.genai.live, "ws_connect"):
        google.genai.live.ws_connect = _patched_ws_connect
except ImportError:
    pass

try:
    import websockets.client
    _original_ws_legacy_connect = websockets.client.connect

    def _patched_ws_legacy_connect(uri, **kwargs):
        if "generativelanguage" in uri:
            kwargs.setdefault("ping_interval", 120)
            kwargs.setdefault("ping_timeout", 120)
        return _original_ws_legacy_connect(uri, **kwargs)

    websockets.client.connect = _patched_ws_legacy_connect
    import google.genai.live
    if hasattr(google.genai.live, "ws_connect") and google.genai.live.ws_connect is _original_ws_legacy_connect:
        google.genai.live.ws_connect = _patched_ws_legacy_connect
except ImportError:
    pass
# -----------------------------------------------------------------

logger = logging.getLogger(__name__)
_debug_logger = logging.getLogger("session_debug")

APP_NAME = "seeme_tutor"

# ---------------------------------------------------------------------------
# Whiteboard queue registry — allows the write_notes tool to push notes
# to the correct client without circular imports.
# ---------------------------------------------------------------------------
_whiteboard_queues: dict[str, asyncio.Queue] = {}
_topic_update_queues: dict[str, asyncio.Queue] = {}


def register_whiteboard_queue(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _whiteboard_queues[session_id] = q
    return q


def get_whiteboard_queue(session_id: str) -> asyncio.Queue | None:
    return _whiteboard_queues.get(session_id)


def unregister_whiteboard_queue(session_id: str) -> None:
    _whiteboard_queues.pop(session_id, None)


def register_topic_update_queue(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _topic_update_queues[session_id] = q
    return q


def get_topic_update_queue(session_id: str) -> asyncio.Queue | None:
    return _topic_update_queues.get(session_id)


def unregister_topic_update_queue(session_id: str) -> None:
    _topic_update_queues.pop(session_id, None)

class ADKLiveSession:
    """
    Async context manager for a single ADK-backed Gemini Live session.

    Presents the same public interface as the old GeminiLiveSession so that
    main.py's forwarding logic works without changes.

    Usage:
        async with ADKLiveSession(runner=runner, user_id=uid, session_id=sid) as session:
            session.send_audio(pcm_bytes)
            async for event_dict in session.receive():
                ...
    """

    def __init__(self, runner: Runner, user_id: str, session_id: str) -> None:
        self._runner = runner
        self._user_id = user_id
        self._session_id = session_id
        self._queue: Optional[LiveRequestQueue] = None

    async def __aenter__(self) -> "ADKLiveSession":
        self._queue = LiveRequestQueue()
        logger.info(
            "ADK live session ready (user=%s, session=%s)",
            self._user_id,
            self._session_id,
        )
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._queue is not None:
            self._queue.close()
            logger.info("ADK live queue closed (session=%s)", self._session_id)
        self._queue = None

    def send_audio(self, audio_bytes: bytes) -> None:
        """Forward a raw PCM audio chunk (16-bit, 16 kHz) to the live session."""
        if self._queue is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        self._queue.send_realtime(
            types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
        )

    def send_video_frame(self, jpeg_bytes: bytes) -> None:
        """Forward a JPEG-encoded video frame to the live session."""
        if self._queue is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        self._queue.send_realtime(
            types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
        )

    def send_text(self, text: str, role: str = "user") -> None:
        """Forward a text instruction/message to the live session."""
        if self._queue is None:
            raise RuntimeError("Session is not open. Use as async context manager.")

        normalized = str(text or "").strip()
        if not normalized:
            return

        self._queue.send_content(
            types.Content(
                role=role,
                parts=[types.Part(text=normalized)],
            )
        )

    def send_activity_start(self) -> None:
        """Signal that the user started speaking (helps barge-in handling)."""
        if self._queue is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        self._queue.send_activity_start()

    def send_activity_end(self) -> None:
        """Signal that the user finished speaking."""
        if self._queue is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        self._queue.send_activity_end()

    async def receive(self) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields response events from the ADK live session.

        Yields dicts with the same shapes as the old GeminiLiveSession:
            {"type": "audio", "data": bytes}
            {"type": "text", "data": str}
            {"type": "turn_complete"}
            {"type": "interrupted"}
        """
        if self._queue is None:
            raise RuntimeError("Session is not open. Use as async context manager.")

        run_config = RunConfig(
            streaming_mode=StreamingMode.BIDI,
            response_modalities=["AUDIO"],
            realtime_input_config=types.RealtimeInputConfig(
                automaticActivityDetection=types.AutomaticActivityDetection(
                    startOfSpeechSensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    prefixPaddingMs=300,
                    silenceDurationMs=500,
                ),
            ),
        )

        event_count = 0
        t_start = time.time()

        async for event in self._runner.run_live(
            user_id=self._user_id,
            session_id=self._session_id,
            live_request_queue=self._queue,
            run_config=run_config,
        ):
            event_count += 1
            # Translate ADK Event objects into the dict format main.py expects.
            try:
                # Log raw event attributes for debugging speech detection issues
                has_content = bool(event.content and event.content.parts)
                content_role = getattr(event.content, "role", None) if event.content else None
                logger.debug(
                    "ADK event #%d: interrupted=%s turn_complete=%s has_content=%s role=%s",
                    event_count,
                    event.interrupted,
                    event.turn_complete,
                    has_content,
                    content_role,
                )
                # Log tool calls/responses for debugging silence issues
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        fc = getattr(part, "function_call", None)
                        if fc:
                            _debug_logger.debug(
                                "TOOL_CALL sid=%s fn=%s t=%.2fs",
                                self._session_id[:8],
                                getattr(fc, "name", "?"),
                                time.time() - t_start,
                            )
                        fr = getattr(part, "function_response", None)
                        if fr:
                            _debug_logger.debug(
                                "TOOL_RESPONSE sid=%s fn=%s t=%.2fs",
                                self._session_id[:8],
                                getattr(fr, "name", "?"),
                                time.time() - t_start,
                            )

                if event.interrupted:
                    logger.info("ADK event #%d: INTERRUPTED", event_count)
                    yield {"type": "interrupted"}
                    continue

                if event.content and event.content.parts:
                    for part in event.content.parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data is not None and inline_data.data:
                            yield {"type": "audio", "data": inline_data.data}
                            continue

                        text = getattr(part, "text", None)
                        if text:
                            # Check if this is an input transcript (what the model heard)
                            if content_role == "user":
                                logger.info(
                                    "ADK event #%d: INPUT TRANSCRIPT (student): %s",
                                    event_count, text,
                                )
                                yield {"type": "input_transcript", "data": text}
                            else:
                                logger.info(
                                    "ADK event #%d: OUTPUT TEXT (tutor): %s",
                                    event_count, text,
                                )
                                yield {"type": "text", "data": text}

                if event.turn_complete:
                    logger.info("ADK event #%d: TURN COMPLETE", event_count)
                    yield {"type": "turn_complete"}

            except (AttributeError, TypeError, KeyError) as parse_exc:
                logger.exception(
                    "Failed to parse ADK event #%d — skipping (event=%r): %s",
                    event_count,
                    event,
                    parse_exc,
                )
                continue
