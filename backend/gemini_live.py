"""
Gemini Live session management for SeeMe Tutor.

Uses the google-genai SDK directly to manage a Live API session via
client.aio.live.connect(). Translates Gemini server messages into the
same dict format that main.py already consumes.
"""

import asyncio
import inspect
import json
import logging
import time
from types import TracebackType
from typing import AsyncGenerator, Optional

from google import genai
from google.genai import types

from tutor_agent.agent import (
    GOOGLE_SEARCH_TOOL,
    MODEL,
    SYSTEM_PROMPT,
    TOOL_DECLARATIONS,
    TOOL_FUNCTIONS,
)

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


class GeminiLiveSession:
    """
    Async context manager for a direct Gemini Live API session.

    Usage:
        async with GeminiLiveSession(state=session_state) as session:
            await session.send_audio(pcm_bytes)
            async for event_dict in session.receive():
                ...
    """

    def __init__(self, state: dict) -> None:
        self._state = state
        self._client = genai.Client()
        self._session = None  # set in __aenter__
        self._session_cm = None  # the context manager from live.connect()

    async def __aenter__(self) -> "GeminiLiveSession":
        student_context = {
            "student_name": self._state.get("student_name"),
            "preferred_language": self._state.get("preferred_language"),
            "resume_message": self._state.get("resume_message"),
            "topic_title": self._state.get("topic_title"),
            "topic_status": self._state.get("topic_status"),
            "language_contract": self._state.get("language_contract"),
            "previous_notes_count": len(self._state.get("previous_notes", [])),
        }

        system_instruction = (
            SYSTEM_PROMPT
            + "\n\n## Current Session\n"
            + json.dumps(student_context, ensure_ascii=False)
        )

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
                parts=[types.Part(text=system_instruction)],
            ),
            tools=[TOOL_DECLARATIONS, GOOGLE_SEARCH_TOOL],
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

        self._session_cm = self._client.aio.live.connect(
            model=MODEL,
            config=config,
        )
        self._session = await self._session_cm.__aenter__()
        logger.info("Gemini Live session connected (model=%s)", MODEL)
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(exc_type, exc_val, exc_tb)
            except Exception:
                logger.debug("Error closing Gemini session", exc_info=True)
            logger.info("Gemini Live session closed")
        self._session = None
        self._session_cm = None

    async def send_audio(self, audio_bytes: bytes) -> None:
        if self._session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        await self._session.send_realtime_input(
            audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
        )

    async def send_video_frame(self, jpeg_bytes: bytes) -> None:
        if self._session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        await self._session.send_realtime_input(
            video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
        )

    async def send_text(self, text: str) -> None:
        if self._session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        normalized = str(text or "").strip()
        if not normalized:
            return
        await self._session.send_realtime_input(
            text=normalized,
        )

    async def send_activity_start(self) -> None:
        if self._session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        await self._session.send_realtime_input(
            activity_start=types.ActivityStart(),
        )

    async def send_activity_end(self) -> None:
        if self._session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        await self._session.send_realtime_input(
            activity_end=types.ActivityEnd(),
        )

    async def receive(self) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields response events from the Gemini Live session.

        Yields dicts with shapes:
            {"type": "audio", "data": bytes}
            {"type": "text", "data": str}
            {"type": "input_transcript", "data": str}
            {"type": "turn_complete"}
            {"type": "interrupted"}
        """
        if self._session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")

        event_count = 0
        t_start = time.time()
        turn_index = 0

        while True:
            try:
                turn_index += 1
                turn_events = 0
                async for msg in self._session.receive():
                    event_count += 1
                    turn_events += 1

                    # --- Tool calls ---
                    tool_call = getattr(msg, "tool_call", None)
                    if tool_call is not None:
                        function_calls = getattr(tool_call, "function_calls", None) or []
                        tool_responses = []
                        for fc in function_calls:
                            fn_name = fc.name
                            fn_args = dict(fc.args) if fc.args else {}
                            _debug_logger.debug(
                                "TOOL_CALL fn=%s args=%s t=%.2fs",
                                fn_name, json.dumps(fn_args)[:200], time.time() - t_start,
                            )
                            result = await self._dispatch_tool(fn_name, fn_args)
                            _debug_logger.debug(
                                "TOOL_RESPONSE fn=%s result=%s t=%.2fs",
                                fn_name, json.dumps(result)[:200], time.time() - t_start,
                            )
                            tool_responses.append(
                                types.FunctionResponse(
                                    name=fn_name,
                                    id=fc.id,
                                    response=result,
                                )
                            )
                        await self._session.send_tool_response(
                            function_responses=tool_responses,
                        )
                        continue

                    # --- Server content ---
                    server_content = getattr(msg, "server_content", None)
                    if server_content is None:
                        continue

                    # Check for interruption
                    if getattr(server_content, "interrupted", False):
                        logger.info("Event #%d: INTERRUPTED", event_count)
                        yield {"type": "interrupted"}
                        continue

                    # Check turn_complete
                    turn_complete = getattr(server_content, "turn_complete", False)

                    # Process content parts
                    model_turn = getattr(server_content, "model_turn", None)
                    if model_turn is not None:
                        parts = getattr(model_turn, "parts", None) or []
                        for part in parts:
                            inline_data = getattr(part, "inline_data", None)
                            if inline_data is not None and inline_data.data:
                                yield {"type": "audio", "data": inline_data.data}
                                continue

                            text = getattr(part, "text", None)
                            if text:
                                logger.info("Event #%d: OUTPUT TEXT (tutor): %s", event_count, text)
                                yield {"type": "text", "data": text}

                    # Input transcription (what the model heard from the student)
                    input_transcription = getattr(server_content, "input_transcription", None)
                    if input_transcription is not None:
                        transcript_text = getattr(input_transcription, "text", None)
                        if transcript_text:
                            logger.info("Event #%d: INPUT TRANSCRIPT (student): %s", event_count, transcript_text)
                            yield {"type": "input_transcript", "data": transcript_text}

                    # Output transcription (model's own speech as text)
                    output_transcription = getattr(server_content, "output_transcription", None)
                    if output_transcription is not None:
                        transcript_text = getattr(output_transcription, "text", None)
                        if transcript_text:
                            logger.info("Event #%d: OUTPUT TRANSCRIPT (tutor): %s", event_count, transcript_text)
                            yield {"type": "text", "data": transcript_text}

                    if turn_complete:
                        logger.info("Event #%d: TURN COMPLETE", event_count)
                        yield {"type": "turn_complete"}

                # google-genai>=1.64.0 returns one model turn per `receive()` call.
                # Re-enter receive() for the next turn instead of treating this as
                # full-session closure.
                logger.debug(
                    "Gemini receive turn #%d ended after %d total events (elapsed=%.2fs)",
                    turn_index,
                    event_count,
                    time.time() - t_start,
                )
                if turn_events == 0:
                    logger.info("Gemini receive stream ended after %d events", event_count)
                    return
                await asyncio.sleep(0)
                continue

            except Exception as exc:
                logger.exception("Error in Gemini receive loop: %s", exc)
                raise

    async def _dispatch_tool(self, name: str, args: dict) -> dict:
        fn = TOOL_FUNCTIONS.get(name)
        if fn is None:
            logger.warning("Unknown tool call: %s", name)
            return {"result": "error", "detail": f"Unknown tool: {name}"}

        try:
            result = fn(**args, state=self._state)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception:
            logger.exception("Tool %s raised an exception", name)
            return {"result": "error", "detail": f"Tool {name} failed internally."}
