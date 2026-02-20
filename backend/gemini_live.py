"""
ADK Live session management for SeeMe Tutor.

Wraps the ADK Runner's live streaming mode, translating ADK Events into the
same dict format that main.py already consumes. The frontend WebSocket
protocol does not change at all.
"""

import logging
from types import TracebackType
from typing import AsyncGenerator, Optional

from google.adk import Runner
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.runners import RunConfig
from google.adk.flows.llm_flows.base_llm_flow import StreamingMode
from google.genai import types

logger = logging.getLogger(__name__)

APP_NAME = "seeme_tutor"


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
        )

        async for event in self._runner.run_live(
            user_id=self._user_id,
            session_id=self._session_id,
            live_request_queue=self._queue,
            run_config=run_config,
        ):
            # Translate ADK Event objects into the dict format main.py expects.
            try:
                if event.interrupted:
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
                            yield {"type": "text", "data": text}

                if event.turn_complete:
                    yield {"type": "turn_complete"}

            except (AttributeError, TypeError, KeyError) as parse_exc:
                logger.exception(
                    "Failed to parse ADK event — skipping (event=%r): %s",
                    event,
                    parse_exc,
                )
                continue
