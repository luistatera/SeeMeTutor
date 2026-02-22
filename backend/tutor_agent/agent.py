"""
SeeMe Tutor — agent tools and system prompt.

Defines Socratic tutoring tools, tool declarations for the Gemini Live API,
and Google Search for factual grounding.
"""

import inspect
import logging
import time

from google.genai import types
import os
try:
    from google.cloud import firestore
    _firestore_available = True
except ImportError:
    _firestore_available = False

logger = logging.getLogger(__name__)

MODEL = "gemini-2.0-flash-live-001"
STRUGGLE_CHECKPOINT_THRESHOLD = 2
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "seeme-tutor")

firestore_client = None

def get_firestore_client():
    global firestore_client
    if not _firestore_available:
        return None
    if firestore_client is None:
        try:
            firestore_client = firestore.AsyncClient(project=GCP_PROJECT_ID)
            logger.info("Async Firestore client lazily initialized in agent.py (project=%s)", GCP_PROJECT_ID)
        except Exception:
            logger.error("Failed to initialize Async Firestore client", exc_info=True)
    return firestore_client

# ---------------------------------------------------------------------------
# Phase-based instruction
# ---------------------------------------------------------------------------

_BASE_INSTRUCTION = """\
You are SeeMe, a warm, patient, and encouraging tutor. You speak like a favorite \
teacher — enthusiastic but never rushed. Your name is SeeMe because you see the \
student's homework, hear their questions, and speak their language.

## Session Phase System

Your session has four phases: greeting, capture, tutoring, review. You start \
in the **greeting** phase. Follow ONLY the instructions for your current phase. \
When the right moment arrives, call `set_session_phase` to transition — the \
tool response will confirm the new phase and remind you of its instructions.

## Handling Interruptions

If the student interrupts you at any point, IMMEDIATELY stop speaking. \
Acknowledge the interruption warmly: "Got it, let me back up" or "Of course, \
what's on your mind?" or "Sure, let's look at that differently." Then \
re-approach from a fresh angle based on what they said. Never finish a sentence \
after being interrupted.

## Language Matching

You are ONLY allowed to respond in three languages: English, Portuguese \
(European or Brazilian), and German. You MUST NEVER respond in any other \
language — not Arabic, not French, not Spanish, not any other language, \
regardless of what you think you hear. If the student's speech is ambiguous or \
you are uncertain which language they are speaking, default to English.

If a student speaks to you in an unsupported language, respond warmly in \
English: "I can help you in English, Portuguese, or German — which would you \
prefer?"

At the beginning of each session, a [SESSION START] message is sent containing \
the student's preferred_language, language_contract, and other context. Use this \
immediately to greet the student — do not call get_backlog_context at session \
start. The language_contract in that message is mandatory and overrides generic \
language behavior. Use get_backlog_context only to refresh context mid-session.

Hard turn-level rule: use one language per tutor turn. Never mix two languages \
in one response unless the language_contract explicitly allows it.

When changing languages, add one short transition sentence first, then continue \
fully in the new language.

For guided bilingual language learning (for example German A2): explain \
strategy in L1, run drills in L2, then return to a short L1 recap based on the \
contract settings. Gently correct errors by modeling the correct form in a \
follow-up question, not by stating "that was wrong."

## Safety and Scope

You are an educational tutor only. If a student asks about something outside of \
learning and homework help, respond warmly but redirect: "That's an interesting \
question, but I'm here to help with your studies — shall we get back to \
[topic]?" Never engage with inappropriate, harmful, or off-topic requests \
beyond a gentle redirection.

## Response Style

Keep responses concise: 2 to 3 sentences for guidance and hints. Use longer \
responses only when introducing a new concept for the first time or when a \
student explicitly asks for a fuller explanation. Speak naturally, as you would \
in a real conversation — avoid lists or bullet points in your spoken responses. \
Match the student's energy: be more playful with younger students, more \
collegial with older ones.

## Grounding Rules

Only reference content you can clearly see in the current camera frame. If \
asked about something not visible, say "I can't see that right now — can you \
show me?" Never fabricate what the student has written — if the image is \
unclear, ask them to show it more clearly.

You have access to a Google Search tool, but you must NEVER use it unless the \
student explicitly asks you to search for something using phrases like "Google", \
"Search for", or "Look up". For all other questions, including math, logic, \
language grammar, translation, and pronunciation, you must rely entirely on your \
internal knowledge and answer immediately without searching. Accuracy matters, \
but avoid trivial lookups to conserve API costs."""

_PHASE_GREETING = """\
## Greeting Phase

1. Read the student context from the [SESSION START] message. Do NOT call \
get_backlog_context — the context is already provided. Start speaking immediately.
2. Greet the student by name in their preferred_language.
3. Reference what they worked on last time (use resume_message from the context).
4. If previous_notes_count > 0, the student has unfinished exercises on the board. \
Tell them: "I see we still have [N] exercises from last time. Want to continue \
where we left off, or show me new homework?" If they want to continue, call \
`set_session_phase("tutoring")` directly — the exercises are already on the whiteboard.
5. Invite them to show their homework on camera OR pick a topic to work on verbally.
6. Keep it brief — one warm greeting, one invitation to start.

### Transitions
- If previous_notes exist and student wants to continue → call `set_session_phase("tutoring")`.
- If the camera shows exercises or a homework page → call `set_session_phase("capture")`.
- If the student picks a topic verbally without showing homework → call `set_session_phase("tutoring")`."""

_PHASE_CAPTURE = """\
## Capture Phase

Your ONLY job right now is to capture every exercise visible on camera. Do NOT \
start teaching yet.

### First-time capture (from greeting)
1. Exercises from previous sessions are already on the board. Do NOT re-capture \
them. Only call `write_notes` for NEW exercises not already present.

### Re-capture (from tutoring — new homework shown)
When you enter capture from the tutoring phase, the board has already been cleared \
automatically. Treat everything on camera as new — capture ALL visible exercises \
without checking previous_notes.

### Capture steps
1. Read EVERY exercise or problem visible on the page.
2. Call `write_notes` once for EACH exercise with `note_type="checklist_item"`, \
using a short title (e.g. "Exercise 1", "Problem 3a") and the exercise text as content.
3. Tell the student: "I can see your exercises — I've added them to our board. \
Which one should we start with?"
4. If the image is unclear or you cannot read it: "I can't quite make that out — \
could you move the camera a little closer to your work?" Wait for a clearer frame \
before calling write_notes.
5. Do NOT explain, hint, or teach during this phase — capture only.

### Transitions
- Once all visible exercises are captured AND the student picks one (or you suggest \
starting from the first) → call `set_session_phase("tutoring")`."""

_PHASE_TUTORING = """\
## Tutoring Phase

This is the core teaching phase. Guide the student through exercises using the \
Socratic method.

### Core Teaching Philosophy

You NEVER give answers directly. Always guide the student to discover the answer \
themselves through questions and hints. Progress through hints only if the student \
is genuinely stuck:
1. First, ask a guiding question that points toward the concept ("What do you \
think happens when we multiply both sides by the same number?")
2. If still stuck, offer a bigger hint framed as a question ("Remember, if \
x + 3 = 7, what do we need to do to isolate x?")
3. If still stuck, give a direct clue — still as a question ("What is 7 minus 3?")

Always celebrate each correct step before moving forward. Even partial \
understanding deserves genuine encouragement.

### Emotional Adaptation

Detect frustration signals: repeated confusion ("I don't get it" said multiple \
times), sighs, rising tension in voice, or three consecutive failed attempts. \
When you detect frustration:
- Slow down noticeably
- Simplify your language
- Offer genuine encouragement: "You're really close — this part is genuinely \
tricky" or "You've already understood the hardest part"
- Break the problem into even smaller steps

Detect confidence: the student answers quickly, correctly, and enthusiastically. \
When you detect confidence, increase the challenge: ask a follow-up question that \
extends the concept, or introduce a related harder variant.

### Curiosity Stimulation

Spark and sustain the student's natural curiosity throughout the session. When a \
student solves a problem, connect it to something bigger: "Nice — now here's the \
cool part: this same idea shows up in [related real-world context]." Ask "what if" \
questions to extend their thinking: "What if the number were negative instead?" or \
"What would change if we used a different unit?" When a student seems disengaged, \
find an angle that connects the topic to their interests or daily life.

### Metacognitive Development

Help the student become aware of their own thinking process. Periodically prompt \
them to reflect: "Before we solve this, what do you think the first step should \
be?" or "You got that one — what strategy did you use?" When wrapping up a topic, \
ask the student to summarize what they learned in their own words. If they get \
stuck, help them identify where they got lost: "Let's trace back — which step \
felt clear and where did it get fuzzy?" This builds independent learning skills, \
not just subject knowledge.

### Visual Grounding

When the camera is active, actively reference what you see in the student's work:
- "I can see you wrote [what you observe] — can you walk me through that step?"
- "Looking at your diagram, what does that arrow represent?"
- "In line 3 of your working, I see a number — what did you do to get there?"

If the image is unclear or you cannot read it: "I can't quite make that out — \
could you move the camera a little closer to your work?" Never guess at content \
you cannot see clearly.

### Exercise Tracking

When starting an exercise, call `update_note_status(note_id, "in_progress")`.
When the student solves it, call `update_note_status(note_id, "done")` or \
`"mastered"`, then move to the next one. If the student is stuck, call \
`update_note_status(note_id, "struggling")`, then simplify your approach.

You MUST call `update_note_status` before moving between exercises. The student \
sees these status changes on their board — it gives them a sense of progress.

### Supporting Notes

Use `write_notes` for supporting material during tutoring:
- `note_type="formula"` — key formulas or equations
- `note_type="vocabulary"` — vocabulary lists or grammar tables
- `note_type="insight"` — key concepts or observations
- `note_type="summary"` — topic summaries

Keep 1-8 notes per topic. Titles short (2-5 words), content concise.

### Progress Tracking

When you observe a clear learning milestone — the student masters a concept or \
struggles significantly with a topic — call `log_progress` to record it. Only \
call it for genuine milestones, not every interaction.

Use `get_backlog_context` whenever you need to confirm the active student \
profile, learning track, and current topic before deciding what to teach next.

When the student asks to switch to a different topic, or when you and the student \
agree to move on after mastering the current one, call `switch_topic` with the \
new topic_id and topic_title.

If `log_progress` returns `checkpoint_required=true`, ask the student whether to \
solve this now or save for later, and then call `set_checkpoint_decision` with \
now/later.

### Transitions
- If all exercises are done or the student wants to wrap up → call \
`set_session_phase("review")`.
- If the student shows new homework on camera → identify the subject and topic \
from what you see. If the new homework belongs to a different topic than the \
current one, call `switch_topic` first with the new topic_id (use a short \
slug like "fractions" or "dative-case") and topic_title. Then call \
`set_session_phase("capture")` — this automatically clears the board and \
starts a fresh capture. Even if the topic stays the same, call \
`set_session_phase("capture")` to clear the old exercises and capture the \
new page."""

_PHASE_REVIEW = """\
## Review Phase

Summarize the session and celebrate accomplishments.

1. Summarize what the student accomplished — mention specific exercises and \
concepts they mastered.
2. Give specific, genuine praise: not "Good job" but "You figured out that \
factoring trick on your own — that's real progress."
3. Call `log_progress` for any final milestones not yet recorded.
4. Note any unfinished exercises for next time: "We still have Exercise 5 to \
tackle — we'll pick it up next session."
5. Suggest what to work on next session based on what you observed.
6. End warmly in the student's preferred language.

### Transitions
- If the student wants to continue working → call \
`set_session_phase("tutoring")`."""

# ---------------------------------------------------------------------------
# Phase lookup structures
# ---------------------------------------------------------------------------

_PHASE_INSTRUCTIONS: dict[str, str] = {
    "greeting": _PHASE_GREETING,
    "capture": _PHASE_CAPTURE,
    "tutoring": _PHASE_TUTORING,
    "review": _PHASE_REVIEW,
}

_VALID_PHASES = frozenset(_PHASE_INSTRUCTIONS.keys())

_VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "greeting": frozenset({"capture", "tutoring"}),
    "capture": frozenset({"tutoring"}),
    "tutoring": frozenset({"capture", "review"}),
    "review": frozenset({"tutoring"}),
}

# ---------------------------------------------------------------------------
# Full system prompt (all phases included — Live API reads it once at start)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    _BASE_INSTRUCTION
    + "\n\n"
    + _PHASE_GREETING
    + "\n\n"
    + _PHASE_CAPTURE
    + "\n\n"
    + _PHASE_TUTORING
    + "\n\n"
    + _PHASE_REVIEW
)

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def set_session_phase(phase: str, *, state: dict) -> dict:
    """Transition the tutoring session to a new phase.

    Call this when the session should move to a different phase (e.g. from
    greeting to capture, from tutoring to review). The tool response includes
    the full instructions for the new phase as a reminder.

    When transitioning from tutoring to capture (new homework detected), the
    whiteboard is automatically cleared so the student sees a fresh board.

    Args:
        phase: Target phase — one of 'greeting', 'capture', 'tutoring', 'review'.

    Returns:
        A dict confirming the transition, including the new phase instructions.
    """
    from gemini_live import get_whiteboard_queue

    normalized = (phase or "").strip().lower()
    if normalized not in _VALID_PHASES:
        return {
            "result": "error",
            "detail": f"Invalid phase '{phase}'. Must be one of: {', '.join(sorted(_VALID_PHASES))}",
        }

    current_phase = state.get("session_phase", "greeting")
    allowed = _VALID_TRANSITIONS.get(current_phase, frozenset())
    if normalized not in allowed:
        return {
            "result": "error",
            "detail": (
                f"Cannot transition from '{current_phase}' to '{normalized}'. "
                f"Allowed transitions: {', '.join(sorted(allowed)) if allowed else 'none'}"
            ),
        }

    state["session_phase"] = normalized
    logger.info("Phase transition: %s -> %s", current_phase, normalized)

    # When re-entering capture from tutoring, clear the whiteboard so the
    # student sees a fresh board for the new homework.
    board_cleared = False
    if normalized == "capture" and current_phase == "tutoring":
        session_id = state.get("session_id")
        queue = get_whiteboard_queue(session_id)
        if queue:
            queue.put_nowait({"action": "clear"})
            board_cleared = True
        # Reset previous_notes so capture doesn't skip new exercises that
        # happen to share a title with old ones.
        state["previous_notes"] = []
        logger.info("Whiteboard cleared for re-capture (session=%s)", session_id)

    result = {
        "result": "transitioned",
        "previous_phase": current_phase,
        "current_phase": normalized,
        "instructions": (
            f"You are now in the **{normalized}** phase. "
            f"Follow these instructions:\n\n{_PHASE_INSTRUCTIONS[normalized]}"
        ),
    }
    if board_cleared:
        result["board_cleared"] = True
    return result


def get_backlog_context(*, state: dict) -> dict:
    """Return session backlog context loaded during websocket bootstrap."""
    return {
        "student_id": state.get("student_id"),
        "student_name": state.get("student_name"),
        "preferred_language": state.get("preferred_language"),
        "track_id": state.get("track_id"),
        "track_title": state.get("track_title"),
        "topic_id": state.get("topic_id"),
        "topic_title": state.get("topic_title"),
        "topic_status": state.get("topic_status"),
        "available_topics": state.get("available_topics", []),
        "previous_notes": state.get("previous_notes", []),
        "language_policy": state.get("language_policy"),
        "language_contract": state.get("language_contract"),
    }


async def log_progress(topic: str, status: str, *, state: dict) -> dict:
    """Record a student learning milestone.

    Call this when the student clearly masters a concept or struggles
    significantly with a topic.

    Args:
        topic: The subject or concept, e.g. 'long division', 'German dative case'.
        status: The student's current grasp — one of 'mastered', 'struggling', or 'improving'.

    Returns:
        A dict confirming the progress was recorded.
    """
    session_id = state.get("session_id", "unknown")
    student_id = state.get("student_id")
    track_id = state.get("track_id")
    topic_id = state.get("topic_id")
    normalized_status = (status or "").strip().lower()
    t0 = time.time()
    try:
        logger.info("Session %s: logging progress for track '%s', topic '%s'", session_id, track_id, topic)
        
        normalized_status = status.strip().lower()
        if normalized_status not in ["started", "completed", "struggling", "mastered", "improving"]: # Added mastered, improving
            return {"result": "error", "message": f"Invalid status {status}"}
        
        fs_client = get_firestore_client()
        if fs_client:
            try:
                now = time.time()
                progress_ref = fs_client.collection("sessions").document(session_id)
                await progress_ref.collection("progress").add({
                    "student_id": student_id,
                    "track_id": track_id,
                    "topic_id": topic_id,
                    "topic": topic,
                    "status": normalized_status,
                    "timestamp": now,
                })

                checkpoint_required = False
                checkpoint_id = None
                if student_id and track_id and topic_id:
                    topic_ref = (
                        fs_client.collection("students")
                        .document(student_id)
                        .collection("tracks")
                        .document(track_id)
                        .collection("topics")
                        .document(topic_id)
                    )

                    topic_snapshot = await topic_ref.get()
                    topic_data = topic_snapshot.to_dict() if topic_snapshot.exists else {}
                    current_struggle_count = int(topic_data.get("struggle_count", 0) or 0)
                    current_success_count = int(topic_data.get("success_count", 0) or 0)

                    topic_updates: dict = {
                        "last_seen_session_id": session_id,
                        "last_seen_at": now,
                        "updated_at": now,
                    }
                    if normalized_status == "struggling":
                        new_struggle_count = current_struggle_count + 1
                        topic_updates["struggle_count"] = new_struggle_count
                        topic_updates["status"] = "struggling"
                        topic_updates["checkpoint_open"] = new_struggle_count >= STRUGGLE_CHECKPOINT_THRESHOLD
                        if new_struggle_count >= STRUGGLE_CHECKPOINT_THRESHOLD:
                            checkpoint_required = True
                            checkpoint_id = f"{track_id}--{topic_id}"
                            checkpoint_reason = f"struggle_count_reached_{new_struggle_count}"
                            checkpoint_ref = (
                                fs_client.collection("students")
                                .document(student_id)
                                .collection("checkpoints")
                                .document(checkpoint_id)
                            )
                            await checkpoint_ref.set({
                                "topic_id": topic_id,
                                "track_id": track_id,
                                "topic_title": state.get("topic_title", topic),
                                "status": "open",
                                "decision": "pending",
                                "trigger": checkpoint_reason,
                                "created_at": now,
                                "updated_at": now,
                                "session_id": session_id,
                            }, merge=True)
                    elif normalized_status == "mastered":
                        topic_updates["success_count"] = current_success_count + 1
                        topic_updates["status"] = "mastered"
                        topic_updates["checkpoint_open"] = False
                        checkpoint_id = f"{track_id}--{topic_id}"
                        await fs_client.collection("students").document(student_id).collection("checkpoints").document(checkpoint_id).set({
                            "status": "resolved",
                            "decision": "resolved",
                            "updated_at": now,
                            "resolved_at": now,
                        }, merge=True)
                    elif normalized_status == "improving":
                        topic_updates["success_count"] = current_success_count + 1
                        topic_updates["status"] = "in_progress"

                    await topic_ref.set(topic_updates, merge=True)
                    await fs_client.collection("students").document(student_id).set({
                        "last_active_topic_id": topic_id,
                        "updated_at": now,
                    }, merge=True)
            except Exception:
                logger.exception("Session %s: failed to write progress to Firestore", session_id)
                return {"result": "error", "detail": "Progress could not be saved — please continue the session normally."}
        else:
            logger.info("Firestore not available — progress not persisted (OK for local dev)")

        response = {"result": "saved", "topic": topic, "status": normalized_status}
        if checkpoint_required:
            response.update({
                "checkpoint_required": True,
                "checkpoint_id": checkpoint_id,
                "prompt": "This topic has been difficult twice. Ask the student if they want to solve it now or save it for later, then call set_checkpoint_decision.",
            })
        return response
    finally:
        logger.info("TOOL_METRIC session=%s tool=log_progress duration_ms=%.1f", session_id, (time.time() - t0) * 1000)


async def set_checkpoint_decision(decision: str, *, state: dict) -> dict:
    """Persist learner decision for the current topic checkpoint."""
    session_id = state.get("session_id", "unknown")
    student_id = state.get("student_id")
    track_id = state.get("track_id")
    topic_id = state.get("topic_id")
    t0 = time.time()
    try:
        normalized_decision = (decision or "").strip().lower()
        if normalized_decision not in {"now", "later", "resolved"}:
            return {"result": "error", "detail": "decision must be one of: now, later, resolved"}

        if not student_id or not track_id or not topic_id:
            return {"result": "error", "detail": "missing checkpoint context in session state"}

        checkpoint_id = f"{track_id}--{topic_id}"
        now = time.time()
        fs_client = get_firestore_client()
        if fs_client:
            try:
                checkpoint_ref = (
                    fs_client.collection("students")
                    .document(student_id)
                    .collection("checkpoints")
                    .document(checkpoint_id)
                )
                checkpoint_status = "open"
                topic_status = "struggling"
                checkpoint_open = True
                if normalized_decision == "now":
                    checkpoint_status = "in_progress"
                    topic_status = "in_progress"
                elif normalized_decision == "later":
                    checkpoint_status = "deferred"
                    topic_status = "struggling"
                elif normalized_decision == "resolved":
                    checkpoint_status = "resolved"
                    topic_status = "mastered"
                    checkpoint_open = False

                await checkpoint_ref.set({
                    "status": checkpoint_status,
                    "decision": normalized_decision,
                    "updated_at": now,
                    "decision_at": now,
                }, merge=True)
                await fs_client.collection("students").document(student_id).collection("tracks").document(track_id).collection("topics").document(topic_id).set({
                    "status": topic_status,
                    "checkpoint_open": checkpoint_open,
                    "updated_at": now,
                }, merge=True)
            except Exception:
                logger.exception("Failed to persist checkpoint decision for %s", checkpoint_id)
                return {"result": "error", "detail": "Could not save checkpoint decision."}
        else:
            logger.info("Firestore not available — checkpoint decision not persisted (OK for local dev)")

        return {
            "result": "saved",
            "checkpoint_id": checkpoint_id,
            "decision": normalized_decision,
        }
    finally:
        logger.info("TOOL_METRIC session=%s tool=set_checkpoint_decision duration_ms=%.1f", session_id, (time.time() - t0) * 1000)


async def write_notes(
    title: str,
    content: str,
    note_type: str = "insight",
    status: str = "pending",
    *,
    state: dict,
) -> dict:
    """Write a note to the student's whiteboard.

    Call this to display key formulas, step-by-step outlines, vocabulary lists,
    checklist items, or summaries on the student's screen.

    Args:
        title: Short heading for the note (2-5 words).
        content: The note body — formulas, steps, or vocabulary.
        note_type: Category of note — one of 'insight', 'checklist_item',
            'formula', 'summary', 'vocabulary'. Defaults to 'insight'.
        status: Initial status — one of 'pending', 'in_progress', 'done',
            'mastered', 'struggling'. Defaults to 'pending'.

    Returns:
        A dict confirming the note was displayed.
    """
    t0 = time.time()
    try:
        from gemini_live import get_whiteboard_queue

        valid_types = {"insight", "checklist_item", "formula", "summary", "vocabulary"}
        valid_statuses = {"pending", "in_progress", "done", "mastered", "struggling"}
        note_type = note_type.strip().lower() if note_type else "insight"
        if note_type not in valid_types:
            note_type = "insight"
        status = status.strip().lower() if status else "pending"
        if status not in valid_statuses:
            status = "pending"

        # Check if a note with the same title already exists (loaded from previous session)
        normalized_title = title.strip().lower()
        for prev in state.get("previous_notes", []):
            if str(prev.get("title", "")).strip().lower() == normalized_title:
                existing_id = prev.get("id", "")
                logger.info("write_notes: skipping duplicate — '%s' already on board as %s", title.strip(), existing_id)
                return {"result": "already_exists", "title": title, "note_id": existing_id, "note_type": note_type, "status": prev.get("status", status)}

        session_id = state.get("session_id")
        note_id = f"note-{int(time.time() * 1000)}"
        queue = get_whiteboard_queue(session_id)
        if queue:
            note = {
                "id": note_id,
                "title": title.strip(),
                "content": content.strip(),
                "note_type": note_type,
                "status": status,
            }
            queue.put_nowait(note)
            logger.info("Session %s: whiteboard note queued — %s [%s/%s]", session_id, title.strip(), note_type, status)

        # Persist to Firestore
        student_id = state.get("student_id")
        track_id = state.get("track_id")
        topic_id = state.get("topic_id")
        fs_client = get_firestore_client()
        if fs_client:
            try:
                now = time.time()
                await fs_client.collection("sessions").document(session_id).collection("notes").document(note_id).set({
                    "title": title.strip(),
                    "content": content.strip(),
                    "note_type": note_type,
                    "status": status,
                    "student_id": student_id,
                    "track_id": track_id,
                    "topic_id": topic_id,
                    "source": "tutor",
                    "created_at": now,
                    "updated_at": now,
                })
            except Exception:
                logger.exception("Session %s: failed to persist note to Firestore", session_id)
        else:
            logger.info("Firestore not available — note not persisted (OK for local dev)")

        return {"result": "displayed", "title": title, "note_id": note_id, "note_type": note_type, "status": status}
    finally:
        session_id = state.get("session_id", "unknown")
        logger.info("TOOL_METRIC session=%s tool=write_notes duration_ms=%.1f", session_id, (time.time() - t0) * 1000)


async def update_note_status(note_id: str, status: str, *, state: dict) -> dict:
    """Update the status of an existing whiteboard note.

    Call this to mark checklist items as in_progress, done, mastered, or
    struggling as the student works through them.

    Args:
        note_id: The note identifier returned by write_notes (e.g. 'note-1234567890').
        status: New status — one of 'pending', 'in_progress', 'done',
            'mastered', 'struggling'.

    Returns:
        A dict confirming the status was updated.
    """
    session_id = state.get("session_id")
    t0 = time.time()
    try:
        from gemini_live import get_whiteboard_queue

        valid_statuses = {"pending", "in_progress", "done", "mastered", "struggling"}
        normalized_status = status.strip().lower() if status else "pending"
        if normalized_status not in valid_statuses:
            return {"result": "error", "detail": f"status must be one of: {', '.join(sorted(valid_statuses))}"}

        fs_client = get_firestore_client()
        if fs_client:
            try:
                now = time.time()
                await fs_client.collection("sessions").document(session_id).collection("notes").document(note_id).set({
                    "status": normalized_status,
                    "updated_at": now,
                }, merge=True)
            except Exception:
                logger.exception("Session %s: failed to update note status in Firestore", session_id)
        else:
            logger.info("Firestore not available — note status not persisted (OK for local dev)")

        queue = get_whiteboard_queue(session_id)
        if queue:
            queue.put_nowait({
                "action": "update_status",
                "id": note_id,
                "status": normalized_status,
            })
            logger.info("Session %s: whiteboard status update — %s → %s", session_id, note_id, normalized_status)

        return {"result": "updated", "note_id": note_id, "status": normalized_status}
    finally:
        logger.info("TOOL_METRIC session=%s tool=update_note_status duration_ms=%.1f", session_id, (time.time() - t0) * 1000)


async def switch_topic(topic_id: str, topic_title: str, *, state: dict) -> dict:
    """Switch the active topic for the current session.

    Call this when the student asks to change topic, or after mastering the
    current one and agreeing to move on.

    Args:
        topic_id: The topic identifier, e.g. 'linear-equations', 'separable-verbs'.
        topic_title: Human-readable title, e.g. 'Linear Equations'.

    Returns:
        A dict confirming the topic was switched.
    """
    session_id = state.get("session_id")
    student_id = state.get("student_id")
    track_id = state.get("track_id")
    old_topic_id = state.get("topic_id")
    old_topic = state.get("topic_title", "--")
    t0 = time.time()
    try:
        from gemini_live import get_topic_update_queue, get_whiteboard_queue

        if str(topic_id) == str(old_topic_id):
            return {"result": "noop", "message": "Already on this topic"}

        fs_client = get_firestore_client()
        if fs_client:
            try:
                now = time.time()
                if student_id:
                    await fs_client.collection("students").document(student_id).set({
                        "last_active_topic_id": topic_id,
                        "updated_at": now,
                    }, merge=True)
                if student_id and track_id:
                    await fs_client.collection("students").document(student_id).collection(
                        "tracks"
                    ).document(track_id).collection("topics").document(topic_id).set({
                        "status": "in_progress",
                        "updated_at": now,
                    }, merge=True)
            except Exception:
                logger.exception("Failed to persist topic switch to Firestore")
        else:
            logger.info("Firestore not available — topic switch not persisted (OK for local dev)")

        state["topic_id"] = topic_id
        state["topic_title"] = topic_title
        state["topic_status"] = "in_progress"

        # Push update to frontend via queue
        queue = get_topic_update_queue(session_id)
        if queue:
            queue.put_nowait({
                "topic_id": topic_id,
                "topic_title": topic_title,
            })
        
        # Also update whiteboard queue for topic change
        wb_queue = get_whiteboard_queue(session_id)
        if wb_queue:
            wb_queue.put_nowait({
                "action": "update_topic",
                "topic_id": topic_id,
                "topic_title": topic_title,
            })

        logger.info(
            "Session %s: switched topic from '%s' to '%s' (%s)",
            session_id, old_topic, topic_title, topic_id,
        )
        return {"result": "switched", "topic_id": topic_id, "topic_title": topic_title}
    finally:
        logger.info("TOOL_METRIC session=%s tool=switch_topic duration_ms=%.1f", session_id, (time.time() - t0) * 1000)


# ---------------------------------------------------------------------------
# Tool registry — maps tool names to callables for dispatch
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS: dict[str, callable] = {
    "set_session_phase": set_session_phase,
    "get_backlog_context": get_backlog_context,
    "log_progress": log_progress,
    "set_checkpoint_decision": set_checkpoint_decision,
    "write_notes": write_notes,
    "update_note_status": update_note_status,
    "switch_topic": switch_topic,
}

# ---------------------------------------------------------------------------
# Tool declarations for the Gemini Live API
# ---------------------------------------------------------------------------


def _build_tool_declarations() -> types.Tool:
    """Build FunctionDeclaration list from the tool functions."""
    declarations = []
    for name, fn in TOOL_FUNCTIONS.items():
        sig = inspect.signature(fn)
        properties: dict = {}
        required: list[str] = []
        for param_name, param in sig.parameters.items():
            if param_name == "state":
                continue  # injected, not sent by model
            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                continue
            schema: dict = {"type": "STRING"}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            properties[param_name] = schema

        declarations.append(types.FunctionDeclaration(
            name=name,
            description=(fn.__doc__ or "").split("\n")[0].strip(),
            parameters=types.Schema(
                type="OBJECT",
                properties={k: types.Schema(**v) for k, v in properties.items()},
                required=required,
            ),
        ))
    return types.Tool(function_declarations=declarations)


TOOL_DECLARATIONS: types.Tool = _build_tool_declarations()
GOOGLE_SEARCH_TOOL: types.Tool = types.Tool(google_search=types.GoogleSearch())
