"""
SeeMe Tutor — ADK agent definition.

Defines the root agent with Socratic tutoring instruction, log_progress tool,
and Google Search for factual grounding.
"""

import logging
import time

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
STRUGGLE_CHECKPOINT_THRESHOLD = 2

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

You are ONLY allowed to respond in three languages: English, Portuguese (European or Brazilian), and German. You MUST NEVER respond in any other language — not Arabic, not French, not Spanish, not any other language, regardless of what you think you hear. If the student's speech is ambiguous or you are uncertain which language they are speaking, default to English.

If a student speaks to you in an unsupported language, respond warmly in English: "I can help you in English, Portuguese, or German — which would you prefer?"

At the beginning of each session, call get_backlog_context and read preferred_language, language_policy, and language_contract. The language_contract is mandatory for this student and overrides generic language behavior.

Hard turn-level rule: use one language per tutor turn. Never mix two languages in one response unless the language_contract explicitly allows it.

When changing languages, add one short transition sentence first, then continue fully in the new language.

For guided bilingual language learning (for example German A2): explain strategy in L1, run drills in L2, then return to a short L1 recap based on the contract settings. Gently correct errors by modeling the correct form in a follow-up question, not by stating "that was wrong."

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

## Grounding Rules

Only reference content you can clearly see in the current camera frame. If asked about something not visible, say "I can't see that right now — can you show me?" Never fabricate what the student has written — if the image is unclear, ask them to show it more clearly.

You have access to a Google Search tool, but you must NEVER use it unless the student explicitly asks you to search for something using phrases like "Google", "Search for", or "Look up". For all other questions, including math, logic, language grammar, translation, and pronunciation, you must rely entirely on your internal knowledge and answer immediately without searching. Accuracy matters, but avoid trivial lookups to conserve API costs.

## Progress Tracking

When you observe a clear learning milestone — the student masters a concept or struggles significantly with a topic — call the log_progress function to record it. Only call it for genuine milestones, not every interaction.

Use the get_backlog_context tool whenever you need to confirm the active student profile, learning track, and current topic before deciding what to teach next.

If log_progress returns checkpoint_required=true, ask the student whether to solve this now or save for later, and then call set_checkpoint_decision with now/later.

## Whiteboard Notes
You have a whiteboard visible on the student's screen. Use write_notes to display:
- Key formulas or equations being discussed
- Step-by-step solution outlines
- Vocabulary lists or grammar tables
- Summaries of what was covered
Call write_notes proactively when visual reference helps. 1-3 notes per topic. Keep titles short (2-5 words), content concise."""


def get_backlog_context(tool_context: ToolContext) -> dict:
    """Return session backlog context loaded during websocket bootstrap."""
    return {
        "student_id": tool_context.state.get("student_id"),
        "student_name": tool_context.state.get("student_name"),
        "preferred_language": tool_context.state.get("preferred_language"),
        "track_id": tool_context.state.get("track_id"),
        "track_title": tool_context.state.get("track_title"),
        "topic_id": tool_context.state.get("topic_id"),
        "topic_title": tool_context.state.get("topic_title"),
        "topic_status": tool_context.state.get("topic_status"),
        "language_policy": tool_context.state.get("language_policy"),
        "language_contract": tool_context.state.get("language_contract"),
    }


def log_progress(topic: str, status: str, tool_context: ToolContext) -> dict:
    """Record a student learning milestone.

    Call this when the student clearly masters a concept or struggles
    significantly with a topic.

    Args:
        topic: The subject or concept, e.g. 'long division', 'German dative case'.
        status: The student's current grasp — one of 'mastered', 'struggling', or 'improving'.
        tool_context: Injected by ADK — provides access to session state.

    Returns:
        A dict confirming the progress was recorded.
    """
    session_id = tool_context.state.get("session_id", "unknown")
    student_id = tool_context.state.get("student_id")
    track_id = tool_context.state.get("track_id")
    topic_id = tool_context.state.get("topic_id")
    gcp_project_id = tool_context.state.get("gcp_project_id", "seeme-tutor")
    normalized_status = (status or "").strip().lower()
    logger.info(
        "Session %s: progress — topic=%s status=%s student=%s track=%s topic_id=%s",
        session_id,
        topic,
        normalized_status,
        student_id,
        track_id,
        topic_id,
    )

    checkpoint_required = False
    checkpoint_id = None
    try:
        from google.cloud import firestore as firestore_module

        db = firestore_module.Client(project=gcp_project_id)
        now = time.time()
        progress_ref = db.collection("sessions").document(session_id)
        progress_ref.collection("progress").add({
            "student_id": student_id,
            "track_id": track_id,
            "topic_id": topic_id,
            "topic": topic,
            "status": normalized_status,
            "timestamp": now,
        })

        checkpoint_reason = ""
        if student_id and track_id and topic_id:
            topic_ref = (
                db.collection("students")
                .document(student_id)
                .collection("tracks")
                .document(track_id)
                .collection("topics")
                .document(topic_id)
            )

            topic_snapshot = topic_ref.get()
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
                        db.collection("students")
                        .document(student_id)
                        .collection("checkpoints")
                        .document(checkpoint_id)
                    )
                    checkpoint_ref.set({
                        "topic_id": topic_id,
                        "track_id": track_id,
                        "topic_title": tool_context.state.get("topic_title", topic),
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
                db.collection("students").document(student_id).collection("checkpoints").document(checkpoint_id).set({
                    "status": "resolved",
                    "decision": "resolved",
                    "updated_at": now,
                    "resolved_at": now,
                }, merge=True)
            elif normalized_status == "improving":
                topic_updates["success_count"] = current_success_count + 1
                topic_updates["status"] = "in_progress"

            topic_ref.set(topic_updates, merge=True)
            db.collection("students").document(student_id).set({
                "last_active_topic_id": topic_id,
                "updated_at": now,
            }, merge=True)

    except ImportError:
        logger.info("Firestore not available — progress not persisted (OK for local dev)")
    except Exception:
        logger.exception("Session %s: failed to write progress to Firestore", session_id)
        return {"result": "error", "detail": "Progress could not be saved — please continue the session normally."}

    response = {"result": "saved", "topic": topic, "status": normalized_status}
    if checkpoint_required:
        response.update({
            "checkpoint_required": True,
            "checkpoint_id": checkpoint_id,
            "prompt": "This topic has been difficult twice. Ask the student if they want to solve it now or save it for later, then call set_checkpoint_decision.",
        })
    return response


def set_checkpoint_decision(decision: str, tool_context: ToolContext) -> dict:
    """Persist learner decision for the current topic checkpoint."""
    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in {"now", "later", "resolved"}:
        return {"result": "error", "detail": "decision must be one of: now, later, resolved"}

    student_id = tool_context.state.get("student_id")
    track_id = tool_context.state.get("track_id")
    topic_id = tool_context.state.get("topic_id")
    gcp_project_id = tool_context.state.get("gcp_project_id", "seeme-tutor")
    if not student_id or not track_id or not topic_id:
        return {"result": "error", "detail": "missing checkpoint context in session state"}

    checkpoint_id = f"{track_id}--{topic_id}"
    now = time.time()
    try:
        from google.cloud import firestore as firestore_module

        db = firestore_module.Client(project=gcp_project_id)
        checkpoint_ref = (
            db.collection("students")
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

        checkpoint_ref.set({
            "status": checkpoint_status,
            "decision": normalized_decision,
            "updated_at": now,
            "decision_at": now,
        }, merge=True)
        db.collection("students").document(student_id).collection("tracks").document(track_id).collection("topics").document(topic_id).set({
            "status": topic_status,
            "checkpoint_open": checkpoint_open,
            "updated_at": now,
        }, merge=True)
    except ImportError:
        logger.info("Firestore not available — checkpoint decision not persisted (OK for local dev)")
    except Exception:
        logger.exception("Failed to persist checkpoint decision for %s", checkpoint_id)
        return {"result": "error", "detail": "Could not save checkpoint decision."}

    return {
        "result": "saved",
        "checkpoint_id": checkpoint_id,
        "decision": normalized_decision,
    }


def write_notes(title: str, content: str, tool_context: ToolContext) -> dict:
    """Write a note to the student's whiteboard.

    Call this to display key formulas, step-by-step outlines, vocabulary lists,
    or summaries on the student's screen.

    Args:
        title: Short heading for the note (2-5 words).
        content: The note body — formulas, steps, or vocabulary.
        tool_context: Injected by ADK — provides access to session state.

    Returns:
        A dict confirming the note was displayed.
    """
    from gemini_live import get_whiteboard_queue

    session_id = tool_context.state.get("session_id")
    queue = get_whiteboard_queue(session_id)
    if queue:
        note = {
            "id": f"note-{int(time.time() * 1000)}",
            "title": title.strip(),
            "content": content.strip(),
        }
        queue.put_nowait(note)
        logger.info("Session %s: whiteboard note queued — %s", session_id, title.strip())
    return {"result": "displayed", "title": title}


root_agent = Agent(
    name="seeme_tutor",
    model=MODEL,
    instruction=SYSTEM_PROMPT,
    tools=[get_backlog_context, log_progress, set_checkpoint_decision, write_notes, google_search],
)
