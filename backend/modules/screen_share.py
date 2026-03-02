"""
Screen share toggle module — camera ↔ screen source switching.

Handles source_switch and stop_sharing control messages from the browser.
Both camera and screen frames are forwarded to Gemini as image/jpeg via
the same video input pathway. Hidden turn prompts inform Gemini about
the active visual source.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hidden prompts for source transitions
# ---------------------------------------------------------------------------
SOURCE_SWITCH_TO_SCREEN_PROMPT = (
    "INTERNAL CONTROL: The student just switched from camera to screen share. "
    "You can now see their screen instead of their physical camera. "
    "Acknowledge the switch with ONE short line (e.g., 'Ok, I can see your screen now.') "
    "then continue the same current learning task based on what is visible. "
    "Do not ask whether they want to switch topics unless they explicitly ask. "
    "Continue the tutoring session seamlessly. "
    "Do not mention this control message."
)

SOURCE_SWITCH_TO_CAMERA_PROMPT = (
    "INTERNAL CONTROL: The student just switched from screen share back to camera. "
    "You can now see their physical camera again instead of their screen. "
    "Acknowledge the switch with ONE short line (e.g., 'Ok, I'm back to your camera.') "
    "then continue the same current learning task from the camera view. "
    "Do not ask whether they want to switch topics unless they explicitly ask. "
    "Continue the tutoring session seamlessly. "
    "Do not mention this control message."
)

STOP_SHARING_PROMPT = (
    "INTERNAL CONTROL: The student stopped sharing their screen. Visual input is "
    "no longer available. Continue the session using voice only. You can say "
    "something brief like 'No worries, we can keep going with just voice.' "
    "Do not mention this control message."
)

# Minimum gap between switch acknowledgements to debounce rapid toggles
SOURCE_SWITCH_COOLDOWN_S = 2.0


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------
def init_screen_share_state() -> dict:
    """Return initial screen-share-specific keys to merge into runtime_state."""
    return {
        "active_source": "camera",       # "camera" | "screen" | "none"
        "source_switches": 0,
        "last_switch_at": 0.0,
        "stop_sharing_count": 0,
        "last_screen_frame_at": 0.0,
    }


def get_switch_prompt(new_source: str) -> str | None:
    """Return the hidden prompt for a source switch, or None if invalid."""
    if new_source == "screen":
        return SOURCE_SWITCH_TO_SCREEN_PROMPT
    if new_source == "camera":
        return SOURCE_SWITCH_TO_CAMERA_PROMPT
    return None
