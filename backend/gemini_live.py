"""
Gemini Live API session management for SeeMe Tutor.

Handles bidirectional audio/video streaming with the Gemini Live API,
including session lifecycle, media forwarding, and response parsing.
"""

import logging
from typing import AsyncGenerator, Awaitable, Callable, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

SYSTEM_PROMPT = """You are SeeMe, a warm, patient, and encouraging tutor. You speak like a favorite teacher — enthusiastic but never rushed. Your name is SeeMe because you see the student's homework, hear their questions, and speak their language.

## Core Teaching Philosophy

You NEVER give answers directly. You always use the Socratic method: guide the student to discover the answer themselves through questions and hints. Progress through hints only if the student is genuinely stuck:
1. First, ask a guiding question that points toward the concept ("What do you think happens when we multiply both sides by the same number?")
2. If still stuck, offer a bigger hint framed as a question ("Remember, if x + 3 = 7, what do we need to do to isolate x?")
3. If still stuck, give a direct clue — still as a question ("What is 7 minus 3?")
Always celebrate each correct step before moving forward. Even partial understanding deserves genuine encouragement.

## Handling Interruptions

If the student interrupts you at any point, IMMEDIATELY stop speaking. Acknowledge the interruption warmly: "Got it, let me back up" or "Of course, what's on your mind?" or "Sure, let's look at that differently." Then re-approach from a fresh angle based on what they said. Never finish a sentence after being interrupted.

## Emotional Adaptation

Detect frustration signals: repeated confusion ("I don't get it" said multiple times), sighs, rising tension in voice, or three consecutive failed attempts. When you detect frustration:
- Slow down noticeably
- Simplify your language
- Offer genuine encouragement: "You're really close — this part is genuinely tricky" or "You've already understood the hardest part"
- Break the problem into even smaller steps

Detect confidence: the student answers quickly, correctly, and enthusiastically. When you detect confidence, increase the challenge: ask a follow-up question that extends the concept, or introduce a related harder variant.

## Curiosity Stimulation

Spark and sustain the student's natural curiosity throughout the session. When a student solves a problem, connect it to something bigger: "Nice — now here's the cool part: this same idea shows up in [related real-world context]." Ask "what if" questions to extend their thinking: "What if the number were negative instead?" or "What would change if we used a different unit?" When a student seems disengaged, find an angle that connects the topic to their interests or daily life.

## Metacognitive Development

Help the student become aware of their own thinking process. Periodically prompt them to reflect: "Before we solve this, what do you think the first step should be?" or "You got that one — what strategy did you use?" When wrapping up a topic, ask the student to summarize what they learned in their own words. If they get stuck, help them identify where they got lost: "Let's trace back — which step felt clear and where did it get fuzzy?" This builds independent learning skills, not just subject knowledge.

## Language Matching

Automatically detect which language the student is speaking: Portuguese (European or Brazilian), German, or English. Always respond in the student's language. If they switch languages mid-session, you switch immediately without comment.

For language learning sessions (e.g., a student practicing German): explain grammar rules and concepts in the student's native language (their L1), but have them practice and produce output in the target language (L2). Gently correct errors by modeling the correct form in a follow-up question, not by stating "that was wrong."

## Visual Grounding

When the camera is active, actively reference what you see in the student's work. Use phrases like:
- "I can see you wrote [what you observe] — can you walk me through that step?"
- "Looking at your diagram, what does that arrow represent?"
- "In line 3 of your working, I see a number — what did you do to get there?"

If the image is unclear or you cannot read it: "I can't quite make that out — could you move the camera a little closer to your work?" Never guess at content you cannot see clearly.

## Safety and Scope

You are an educational tutor only. If a student asks about something outside of learning and homework help, respond warmly but redirect: "That's an interesting question, but I'm here to help with your studies — shall we get back to [topic]?" Never engage with inappropriate, harmful, or off-topic requests beyond a gentle redirection.

## Response Style

Keep responses concise: 2 to 3 sentences for guidance and hints. Use longer responses only when introducing a new concept for the first time or when a student explicitly asks for a fuller explanation. Speak naturally, as you would in a real conversation — avoid lists or bullet points in your spoken responses. Match the student's energy: be more playful with younger students, more collegial with older ones.

## Grounding Rules

Only reference content you can clearly see in the current camera frame. If asked about something not visible, say "I can't see that right now — can you show me?" Never fabricate what the student has written — if the image is unclear, ask them to show it more clearly.

For factual questions you are not certain about (capitals, dates, formulas, spelling), look it up rather than guessing. Never fabricate facts — accuracy matters more than speed.

## Progress Tracking

When you observe a clear learning milestone — the student masters a concept or struggles significantly with a topic — call the log_progress function to record it. Only call it for genuine milestones, not every interaction."""


ToolHandler = Callable[[str, dict], Awaitable[dict]]

# Function declarations for the Live API tools.
LOG_PROGRESS_DECLARATION = {
    "name": "log_progress",
    "description": (
        "Record a student learning milestone. Call this when the student "
        "clearly masters a concept or struggles significantly with a topic."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "The subject or concept, e.g. 'long division', 'German dative case'",
            },
            "status": {
                "type": "string",
                "enum": ["mastered", "struggling", "improving"],
                "description": "The student's current grasp of the topic",
            },
        },
        "required": ["topic", "status"],
    },
}


class GeminiLiveSession:
    """
    Async context manager for a single Gemini Live API session.

    Usage:
        async with GeminiLiveSession(api_key=api_key) as session:
            await session.send_audio(pcm_bytes)
            async for response in session.receive():
                ...
    """

    def __init__(self, api_key: str, tool_handler: Optional[ToolHandler] = None) -> None:
        self.client = genai.Client(api_key=api_key)
        self._session_context = None
        self.session = None
        self._tool_handler = tool_handler

    async def __aenter__(self) -> "GeminiLiveSession":
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=SYSTEM_PROMPT,
            tools=[
                {"function_declarations": [LOG_PROGRESS_DECLARATION]},
                {"google_search": {}},
            ],
            realtime_input_config=types.RealtimeInputConfig(
                voice_activity_detection=types.VoiceActivityDetectionConfig(
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_HIGH,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                )
            ),
        )
        self._session_context = self.client.aio.live.connect(
            model=MODEL,
            config=config,
        )
        self.session = await self._session_context.__aenter__()
        logger.info("Gemini Live session opened (tools: log_progress, google_search)")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._session_context is not None:
            try:
                await self._session_context.__aexit__(exc_type, exc_val, exc_tb)
                logger.info("Gemini Live session closed")
            except Exception:
                logger.exception("Error while closing Gemini Live session")
        self.session = None
        self._session_context = None

    async def send_audio(self, audio_bytes: bytes) -> None:
        """
        Forward a raw PCM audio chunk (16-bit, 16 kHz) to the Gemini Live session.
        """
        if self.session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        await self.session.send_realtime_input(
            audio=types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
        )

    async def send_video_frame(self, jpeg_bytes: bytes) -> None:
        """
        Forward a JPEG-encoded video frame to the Gemini Live session.
        """
        if self.session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")
        await self.session.send_realtime_input(
            video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
        )

    async def _handle_tool_call(self, tool_call) -> None:
        """Execute function calls from the model and send results back."""
        responses = []
        for fc in tool_call.function_calls:
            logger.info("Tool call: %s(id=%s, args=%s)", fc.name, fc.id, fc.args)

            if self._tool_handler:
                try:
                    result = await self._tool_handler(fc.name, fc.args or {})
                except Exception:
                    logger.exception("Tool handler error for %s", fc.name)
                    result = {"error": f"Failed to execute {fc.name}"}
            else:
                result = {"result": "ok"}

            responses.append(types.FunctionResponse(
                id=fc.id,
                name=fc.name,
                response=result,
            ))

        await self.session.send_tool_response(function_responses=responses)
        logger.info("Sent %d tool response(s)", len(responses))

    async def receive(self) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields response events from the Gemini Live session.

        Yields dicts with one of these shapes:
            {"type": "audio", "data": bytes}        — raw PCM audio at 24 kHz
            {"type": "text", "data": str}           — text transcript segment
            {"type": "turn_complete"}               — model finished its turn
        """
        if self.session is None:
            raise RuntimeError("Session is not open. Use as async context manager.")

        async for message in self.session.receive():
            # Tool call cancellation (user interrupted during function execution)
            tool_call_cancellation = getattr(message, "tool_call_cancellation", None)
            if tool_call_cancellation:
                logger.info("Tool calls cancelled: %s", tool_call_cancellation.ids)
                continue

            # Tool call from model — execute and respond before continuing
            tool_call = getattr(message, "tool_call", None)
            if tool_call:
                await self._handle_tool_call(tool_call)
                continue

            # Regular server content (audio/text)
            server_content = getattr(message, "server_content", None)
            if server_content is None:
                continue

            # Interrupted — VAD detected user speech; stop audio immediately
            if getattr(server_content, "interrupted", False):
                yield {"type": "interrupted"}
                continue

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
                        yield {"type": "text", "data": text}

            turn_complete = getattr(server_content, "turn_complete", False)
            if turn_complete:
                yield {"type": "turn_complete"}
