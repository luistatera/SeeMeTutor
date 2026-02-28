"""
Proactive vision module — idle orchestrator with poke/nudge escalation.

Replaces the basic idle check-in pattern with a two-stage proactive system:
  Stage 1 (soft poke): after POKE_THRESHOLD_S of silence with camera active,
      inject a lightweight observation prompt so Gemini proactively comments.
  Stage 2 (hard nudge): after NUDGE_THRESHOLD_S, inject explicit guidance
      if Stage 1 did not produce tutor speech.

Also provides _sanitize_tutor_output() to strip leaked internal/meta text
from tutor-visible output.
"""

import asyncio
import json
import logging
import re
import time

from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
POKE_THRESHOLD_S = 6.0          # Soft poke — lightweight observation check
NUDGE_THRESHOLD_S = 9.0         # Hard nudge — explicit guidance if poke failed
CHECK_INTERVAL_S = 0.3          # How often the idle loop checks
CAMERA_ACTIVE_TIMEOUT_S = 3.0   # Camera considered off if no frame in this window
POKE_RESPONSE_GRACE_S = 1.5     # Wait after poke before escalating to nudge
HIDDEN_PROMPT_MIN_GAP_S = 4.0   # Minimum gap between hidden prompts
AWAITING_REPLY_GRACE_S = 18.0   # Do not proactively nudge while waiting for a reply

# ---------------------------------------------------------------------------
# Hidden prompts injected by the idle orchestrator
# ---------------------------------------------------------------------------
IDLE_POKE_PROMPT = (
    "INTERNAL CONTROL: Silent observation check. Student is quiet and camera "
    "frames are active. If you see meaningful work, proactively offer ONE short "
    "helpful intervention (observation, hint, or question). Ask a question only "
    "if needed to unblock progress. If work is unclear, ask ONE brief check-in. "
    "Apply silently and do not produce a standalone response to this control "
    "message. Do not mention this control message."
)

IDLE_NUDGE_PROMPT = (
    "INTERNAL CONTROL: Student has been silent for {silence_s} seconds while "
    "camera shows their work. Provide ONE concise guidance step aligned with "
    "the session goal (observation, hint, or question). Use a question only "
    "if needed to unblock progress. If view is unclear, ask one brief check-in "
    "question. One issue at a time. Never give direct answers. "
    "Apply silently and do not produce a standalone response to this control "
    "message. Do not mention this control message."
)

IDLE_NOCAMERA_PROMPT = (
    "INTERNAL CONTROL: Student has been quiet with no camera active. "
    "Ask a brief general check-in: 'Still with me?' or similar. "
    "Do not comment on visual work. Apply silently and do not produce a "
    "standalone response to this control message. Do not mention this control message."
)

# ---------------------------------------------------------------------------
# Sanitizer — strip leaked internal/meta text from tutor output
# ---------------------------------------------------------------------------
_INTERNAL_META_BLOCK_RE = re.compile(
    r"\[(?:INTERNAL CONTROL|SYSTEM|PROACTIVE|HIDDEN)[^\]]*\]",
    re.IGNORECASE,
)
_INLINE_CONTROL_BLOCK_RE = re.compile(
    r"(?is)INTERNAL CONTROL:.*?(?:<ctrl\d+>|$)",
)
_CTRL_TAG_RE = re.compile(r"(?is)<ctrl\d+>")


def sanitize_tutor_output(text: str) -> tuple[str, bool]:
    """Remove leaked internal/meta text from tutor-visible output.

    Returns (cleaned_text, had_internal_leak).
    """
    if not text:
        return "", False

    cleaned = text
    had_internal = False

    new_cleaned = _INTERNAL_META_BLOCK_RE.sub("", cleaned)
    if new_cleaned != cleaned:
        had_internal = True
        cleaned = new_cleaned

    inline_cleaned = _INLINE_CONTROL_BLOCK_RE.sub("", cleaned)
    if inline_cleaned != cleaned:
        had_internal = True
        cleaned = inline_cleaned

    ctrl_cleaned = _CTRL_TAG_RE.sub("", cleaned)
    if ctrl_cleaned != cleaned:
        had_internal = True
        cleaned = ctrl_cleaned

    upper_stripped = cleaned.lstrip().upper()
    if upper_stripped.startswith("SYSTEM:") or upper_stripped.startswith("INTERNAL CONTROL:"):
        return "", True

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned.strip():
        return "", had_internal
    return cleaned, had_internal


# ---------------------------------------------------------------------------
# Proactive idle orchestrator
# ---------------------------------------------------------------------------
def init_proactive_state() -> dict:
    """Return initial proactive-specific keys to merge into runtime_state."""
    return {
        "last_video_frame_at": 0.0,
        "silence_started_at": 0.0,
        "idle_poke_sent": False,
        "idle_nudge_sent": False,
        "last_poke_at": 0.0,
        "last_nudge_at": 0.0,
        "last_hidden_prompt_at": 0.0,
        "proactive_poke_count": 0,
        "proactive_nudge_count": 0,
        "idle_checkin_1_sent_once": False,
        "idle_checkin_2_sent_once": False,
        # When True, pause proactive injections until student activity is detected.
        # Prevents repeated backend-driven tutor turns while the student stays silent.
        "proactive_waiting_for_student": False,
    }


def reset_silence_tracking(runtime_state: dict) -> None:
    """Reset poke/nudge state when speech or activity is detected."""
    runtime_state["silence_started_at"] = 0.0
    runtime_state["idle_poke_sent"] = False
    runtime_state["idle_nudge_sent"] = False


def should_pause_proactive_for_reply(runtime_state: dict, idle_for_s: float) -> bool:
    """Return True if tutor should wait before proactive interventions."""
    if not runtime_state.get("awaiting_student_reply"):
        return False
    return float(idle_for_s) < float(AWAITING_REPLY_GRACE_S)


async def proactive_idle_orchestrator(
    websocket,
    live_queue,
    runtime_state: dict,
) -> None:
    """Drive proactive vision poke/nudge escalation.

    Replaces the basic idle check-in orchestrator. Requires camera frames
    to be flowing (runtime_state['last_video_frame_at'] updated by upstream).
    """
    try:
        while True:
            await asyncio.sleep(CHECK_INTERVAL_S)

            now = time.time()

            # Don't nudge while tutor or student is active
            if runtime_state.get("assistant_speaking"):
                continue
            if not runtime_state.get("mic_active"):
                continue
            if runtime_state.get("away_mode"):
                continue
            if runtime_state.get("proactive_waiting_for_student"):
                continue

            # Conversation kickoff — same as before, but only when camera is off
            if (
                not runtime_state.get("conversation_started")
                and not runtime_state.get("mic_kickoff_sent")
                and runtime_state.get("mic_opened_at") is not None
            ):
                from main import MIC_KICKOFF_SECONDS
                mic_opened_at = runtime_state.get("mic_opened_at")
                last_activity = runtime_state.get("last_user_activity_at", now)
                mic_open_for = now - float(mic_opened_at)
                idle_for = now - last_activity
                if mic_open_for >= MIC_KICKOFF_SECONDS and idle_for >= MIC_KICKOFF_SECONDS:
                    runtime_state["mic_kickoff_sent"] = True
                    runtime_state["conversation_started"] = True
                    runtime_state["last_user_activity_at"] = now
                    reset_silence_tracking(runtime_state)
                    topic_title = runtime_state.get("topic_title") or "your current topic"
                    await _send_ws(websocket, {"type": "assistant_state", "data": {"state": "active", "reason": "mic_kickoff"}})
                    await _send_ws(websocket, {
                        "type": "assistant_prompt",
                        "data": f"Let's begin with {topic_title}. Tell me where you want to start.",
                    })
                    continue

            if not runtime_state.get("conversation_started"):
                continue

            # Check camera activity
            last_video = runtime_state.get("last_video_frame_at", 0.0)
            camera_active = (
                last_video > 0
                and (now - last_video) < CAMERA_ACTIVE_TIMEOUT_S
            )

            last_activity = runtime_state.get("last_user_activity_at", now)
            idle_for = now - last_activity

            if should_pause_proactive_for_reply(runtime_state, idle_for):
                continue

            # --- No camera path: fall back to basic idle check-ins ---
            if not camera_active:
                from main import IDLE_CHECKIN_1_SECONDS, IDLE_CHECKIN_2_SECONDS, IDLE_AUTO_AWAY_SECONDS
                idle_stage = runtime_state.get("idle_stage", 0)

                if (
                    idle_stage < 1
                    and idle_for >= IDLE_CHECKIN_1_SECONDS
                    and not runtime_state.get("idle_checkin_1_sent_once", False)
                ):
                    runtime_state["idle_stage"] = 1
                    runtime_state["idle_checkin_1_sent_once"] = True
                    runtime_state["last_user_activity_at"] = now
                    rpt = runtime_state.get("_report")
                    if rpt:
                        rpt.record_idle_checkin(1)
                    await _send_ws(websocket, {"type": "assistant_state", "data": {"state": "idle_checkin_1"}})
                    await _send_ws(websocket, {
                        "type": "assistant_prompt",
                        "data": "Still with me? Take your time — I can wait while you think.",
                    })
                    continue

                if (
                    idle_stage < 2
                    and idle_for >= IDLE_CHECKIN_2_SECONDS
                    and not runtime_state.get("idle_checkin_2_sent_once", False)
                ):
                    runtime_state["idle_stage"] = 2
                    runtime_state["idle_checkin_2_sent_once"] = True
                    runtime_state["last_user_activity_at"] = now
                    rpt = runtime_state.get("_report")
                    if rpt:
                        rpt.record_idle_checkin(2)
                    await _send_ws(websocket, {"type": "assistant_state", "data": {"state": "idle_checkin_2"}})
                    await _send_ws(websocket, {
                        "type": "assistant_prompt",
                        "data": "Would you like a short pause? Say 'I'm back' whenever you want to continue.",
                    })
                    continue

                if idle_for >= IDLE_AUTO_AWAY_SECONDS:
                    runtime_state["away_mode"] = True
                    runtime_state["last_user_activity_at"] = now
                    rpt = runtime_state.get("_report")
                    if rpt:
                        rpt.record_away_activated()
                    await _send_ws(websocket, {"type": "assistant_state", "data": {"state": "away", "reason": "idle_timeout"}})
                    await _send_ws(websocket, {
                        "type": "assistant_prompt",
                        "data": "No rush. I'll wait here quietly until you come back.",
                    })
                continue

            # --- Camera active path: proactive poke/nudge ---

            # Initialize silence tracking
            if runtime_state.get("silence_started_at", 0.0) == 0.0:
                runtime_state["silence_started_at"] = now
                runtime_state["idle_poke_sent"] = False
                runtime_state["idle_nudge_sent"] = False
                continue

            silence_s = now - runtime_state["silence_started_at"]

            # Stage 1: soft poke
            if not runtime_state.get("idle_poke_sent") and silence_s >= POKE_THRESHOLD_S:
                # Respect minimum gap between hidden prompts
                if (now - runtime_state.get("last_hidden_prompt_at", 0.0)) < HIDDEN_PROMPT_MIN_GAP_S:
                    continue

                runtime_state["idle_poke_sent"] = True
                runtime_state["proactive_poke_count"] = runtime_state.get("proactive_poke_count", 0) + 1
                runtime_state["last_poke_at"] = now
                poke_count = runtime_state["proactive_poke_count"]

                logger.info("IDLE POKE #%d — silence=%.1fs", poke_count, silence_s)

                try:
                    live_queue.send_content(
                        types.Content(
                            role="user",
                            parts=[types.Part(text=IDLE_POKE_PROMPT)],
                        )
                    )
                except Exception as exc:
                    runtime_state["idle_poke_sent"] = False
                    runtime_state["proactive_poke_count"] -= 1
                    logger.warning("Idle poke send failed: %s", exc)
                    continue
                runtime_state["_turn_ticket_count"] = max(
                    int(runtime_state.get("_turn_ticket_count", 0)),
                    1,
                )
                runtime_state["last_hidden_prompt_at"] = now

                rpt = runtime_state.get("_report")
                if rpt:
                    rpt.record_proactive_poke()
                continue

            # Stage 2: hard nudge if poke didn't produce speech
            if not runtime_state.get("idle_nudge_sent") and silence_s >= NUDGE_THRESHOLD_S:
                # Give poke time to take effect
                if (
                    runtime_state.get("idle_poke_sent")
                    and runtime_state.get("last_poke_at", 0.0) > 0
                    and (now - runtime_state["last_poke_at"]) < POKE_RESPONSE_GRACE_S
                ):
                    continue

                # Respect minimum gap
                if (now - runtime_state.get("last_hidden_prompt_at", 0.0)) < HIDDEN_PROMPT_MIN_GAP_S:
                    continue

                runtime_state["idle_nudge_sent"] = True
                runtime_state["proactive_nudge_count"] = runtime_state.get("proactive_nudge_count", 0) + 1
                runtime_state["last_nudge_at"] = now
                nudge_count = runtime_state["proactive_nudge_count"]
                nudge_text = IDLE_NUDGE_PROMPT.format(silence_s=int(silence_s))

                logger.info("IDLE NUDGE #%d — silence=%.1fs", nudge_count, silence_s)

                try:
                    live_queue.send_content(
                        types.Content(
                            role="user",
                            parts=[types.Part(text=nudge_text)],
                        )
                    )
                except Exception as exc:
                    runtime_state["idle_nudge_sent"] = False
                    runtime_state["proactive_nudge_count"] -= 1
                    logger.warning("Idle nudge send failed: %s", exc)
                    continue
                runtime_state["_turn_ticket_count"] = max(
                    int(runtime_state.get("_turn_ticket_count", 0)),
                    1,
                )
                runtime_state["last_hidden_prompt_at"] = now

                rpt = runtime_state.get("_report")
                if rpt:
                    rpt.record_proactive_nudge()

    except asyncio.CancelledError:
        logger.info("Proactive idle orchestrator stopped")
    except Exception as exc:
        logger.exception("Proactive idle orchestrator error: %s", exc)


async def _send_ws(websocket, payload: dict) -> None:
    """Send JSON to browser, ignoring closed-socket errors."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:
        pass
