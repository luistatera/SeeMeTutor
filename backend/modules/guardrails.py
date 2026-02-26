"""
Safety & scope guardrails — input/output analysis and drift reinforcement.

Three-layer safety:
1. System prompt — absolute rules baked into Gemini instructions
2. Input analysis — pattern matching on student speech for off-topic/cheat/inappropriate
3. Output monitoring — regex scanning of tutor speech for answer leaks + hidden turn reinforcement
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REINFORCE_COOLDOWN_S = 15.0  # Min gap between reinforcement injections
HIDDEN_PROMPT_MIN_GAP_S = 4.0  # Min gap between any hidden prompt sends

# ---------------------------------------------------------------------------
# Regex patterns for guardrail detection
# ---------------------------------------------------------------------------

# Tutor gave a direct answer (output check)
DIRECT_ANSWER_PATTERNS = re.compile(
    r"(?:the answer is|the solution is|it equals|the result is|"
    r"that equals|the correct answer|= \d|here'?s the (answer|solution)|"
    r"the formula is .+ = |simply put,? it'?s)",
    re.IGNORECASE,
)

# Student asked something off-topic (input check)
OFF_TOPIC_PATTERNS = re.compile(
    r"(?:tell me a joke|what'?s the weather|play a game|sing a song|"
    r"tell me a story|what'?s your favorite|do you have feelings|"
    r"are you real|who made you|what are you|how do you work|"
    r"recipe for|how to cook|what'?s on tv|latest news|"
    r"crypto|bitcoin|stock market|bet on)",
    re.IGNORECASE,
)

# Student requesting direct answers / cheating (input check)
CHEAT_PATTERNS = re.compile(
    r"(?:just tell me|give me the answer|do my homework|"
    r"write my essay|solve it for me|just give me|"
    r"finish this for me|complete my assignment)",
    re.IGNORECASE,
)

# Inappropriate content (input check)
INAPPROPRIATE_PATTERNS = re.compile(
    r"(?:how to (make|build) (?:a )?(bomb|weapon|gun)|"
    r"how (?:do i|can i) (make|build) (?:a )?(bomb|weapon|gun)|"
    r"(?:make|build) (?:a )?(bomb|weapon|gun) at home|"
    r"how to (hurt|harm|kill)|drugs|"
    r"explicit|pornograph|sexu|"
    r"hack into|break into|steal|"
    r"suicide|self.?harm)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Reinforcement prompts (injected as hidden turns when model drifts)
# ---------------------------------------------------------------------------
SOCRATIC_REINFORCE_PROMPT = (
    "INTERNAL CONTROL: Guardrail check. Your last response may have come "
    "close to giving a direct answer. Remember: NEVER give the answer. "
    "Guide with hints, questions, and encouragement. If the student asks "
    "'just tell me', redirect with a hint instead. Do not mention this "
    "control message."
)

SCOPE_REINFORCE_PROMPT = (
    "INTERNAL CONTROL: Guardrail check. The student's last request may be "
    "off-topic or outside educational scope. Politely redirect to learning. "
    "Use the refusal template: acknowledge their interest, then redirect to "
    "their subject. Do not mention this control message."
)

CONTENT_MODERATION_PROMPT = (
    "INTERNAL CONTROL: Content flag. The student's input may contain "
    "inappropriate content. Redirect gracefully: 'That's not something I "
    "can help with. But I'm great at math, science, and languages! What "
    "are you studying today?' Do not mention this control message."
)


# ---------------------------------------------------------------------------
# State initialization
# ---------------------------------------------------------------------------
def init_guardrails_state() -> dict:
    """Return initial guardrails state keys to merge into runtime_state."""
    return {
        "guardrail_last_reinforce_at": 0.0,
        "guardrail_refusals_total": 0,
        "guardrail_answer_leaks": 0,
        "guardrail_drift_reinforcements": 0,
        "guardrail_content_flags": 0,
    }


# ---------------------------------------------------------------------------
# Input analysis — student speech
# ---------------------------------------------------------------------------
def check_student_input(text: str) -> list[dict]:
    """Analyze student input for guardrail-relevant patterns.

    Returns a list of guardrail events detected (may be empty).
    """
    events = []

    if INAPPROPRIATE_PATTERNS.search(text):
        events.append({
            "guardrail": "content_moderation",
            "severity": "high",
            "detail": "Inappropriate content detected in student input",
        })

    if CHEAT_PATTERNS.search(text):
        events.append({
            "guardrail": "cheat_request",
            "severity": "medium",
            "detail": "Student requesting direct answers / cheating",
        })

    if OFF_TOPIC_PATTERNS.search(text):
        events.append({
            "guardrail": "off_topic",
            "severity": "low",
            "detail": "Off-topic request detected",
        })

    return events


# ---------------------------------------------------------------------------
# Output analysis — tutor speech
# ---------------------------------------------------------------------------
def check_tutor_output(text: str) -> list[dict]:
    """Analyze tutor output for guardrail violations (answer leaks).

    Returns a list of guardrail events (may be empty).
    """
    events = []

    if DIRECT_ANSWER_PATTERNS.search(text):
        events.append({
            "guardrail": "answer_leak",
            "severity": "high",
            "detail": "Tutor may have given a direct answer",
        })

    return events


# ---------------------------------------------------------------------------
# Reinforcement selection
# ---------------------------------------------------------------------------
def select_reinforcement(
    events: list[dict],
    runtime_state: dict,
) -> str | None:
    """Pick a reinforcement prompt for the given guardrail events.

    Respects cooldown to avoid disrupting the model's flow.
    Returns the prompt string, or None if cooldown hasn't elapsed or no events.
    """
    if not events:
        return None

    now = time.time()
    last = float(runtime_state.get("guardrail_last_reinforce_at", 0.0))
    if (now - last) < HIDDEN_PROMPT_MIN_GAP_S:
        return None

    # Pick by highest severity
    severity_rank = {"high": 3, "medium": 2, "low": 1}
    highest = max(events, key=lambda e: severity_rank.get(e.get("severity", ""), 0))

    if highest.get("severity") == "high":
        if highest.get("guardrail") == "content_moderation":
            return CONTENT_MODERATION_PROMPT
        return SOCRATIC_REINFORCE_PROMPT

    if highest.get("guardrail") == "cheat_request":
        return SOCRATIC_REINFORCE_PROMPT

    if highest.get("guardrail") == "off_topic":
        return SCOPE_REINFORCE_PROMPT

    if highest.get("guardrail") == "answer_leak":
        return SOCRATIC_REINFORCE_PROMPT

    return None


def record_guardrail_event(
    runtime_state: dict,
    event: dict,
    source: str,
) -> None:
    """Record a guardrail event in runtime_state metrics."""
    guardrail = event.get("guardrail", "")

    if guardrail in ("off_topic", "cheat_request", "content_moderation"):
        runtime_state["guardrail_refusals_total"] = (
            runtime_state.get("guardrail_refusals_total", 0) + 1
        )
    if guardrail == "content_moderation":
        runtime_state["guardrail_content_flags"] = (
            runtime_state.get("guardrail_content_flags", 0) + 1
        )
    if guardrail == "answer_leak":
        runtime_state["guardrail_answer_leaks"] = (
            runtime_state.get("guardrail_answer_leaks", 0) + 1
        )

    logger.info(
        "GUARDRAIL [%s] severity=%s source=%s: %s",
        guardrail,
        event.get("severity", ""),
        source,
        event.get("detail", ""),
    )


def record_reinforcement(runtime_state: dict, reason: str) -> None:
    """Mark that a reinforcement was just sent."""
    runtime_state["guardrail_last_reinforce_at"] = time.time()
    runtime_state["guardrail_drift_reinforcements"] = (
        runtime_state.get("guardrail_drift_reinforcements", 0) + 1
    )
    logger.info(
        "GUARDRAIL_REINFORCE reason=%s count=%d",
        reason,
        runtime_state["guardrail_drift_reinforcements"],
    )
