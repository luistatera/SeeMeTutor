"""
Auto-note extraction and storage — question-answer notes and example notes.

Extracted from main.py (Step 6 of refactor).  Takes ``firestore_client``
and ``wb_queue`` as explicit parameters.
"""

import asyncio
import logging
import time
from typing import Any

from modules.conversation import (
    build_example_note,
    build_question_answer_note,
    extract_example_from_turn,
)
from modules.session_helpers import (
    _question_answer_signature,
    _example_signature,
)
from modules.tutor_preferences import QUESTION_NOTE_MAX_AGE_S
from modules.whiteboard import normalize_content, normalize_title

logger = logging.getLogger(__name__)


async def _maybe_store_question_answer_note(
    session_id: str,
    runtime_state: dict,
    turn_text: str,
    wb_queue: asyncio.Queue | None,
    firestore_client: Any | None,
    report: Any | None = None,
) -> None:
    pending = runtime_state.get("pending_study_question")
    if not isinstance(pending, dict):
        return

    question = str(pending.get("text") or "").strip()
    if not question:
        runtime_state["pending_study_question"] = None
        return

    now = time.time()
    asked_at = float(pending.get("asked_at", 0.0) or 0.0)
    if asked_at <= 0 or (now - asked_at) > QUESTION_NOTE_MAX_AGE_S:
        runtime_state["pending_study_question"] = None
        return

    answer = str(turn_text or "").strip()
    if not answer:
        return

    signature = _question_answer_signature(question, answer)
    signatures = runtime_state.setdefault("question_note_signatures", set())
    if signature in signatures:
        runtime_state["pending_study_question"] = None
        return
    signatures.add(signature)
    if len(signatures) > 200:
        signatures.clear()
        signatures.add(signature)

    sequence = int(runtime_state.get("question_note_counter", 0)) + 1
    runtime_state["question_note_counter"] = sequence
    title, content = build_question_answer_note(question, answer, sequence)
    title = normalize_title(title)
    content = normalize_content(content)
    note_id = f"note-{int(now * 1000)}-qa-{sequence}"
    note = {
        "id": note_id,
        "title": title,
        "content": content,
        "note_type": "summary",
        "status": "pending",
    }

    if wb_queue is not None:
        wb_queue.put_nowait(note)
        if report:
            report.record_whiteboard_note_created()
            report.record_whiteboard_note_queued(str(note_id))

    if firestore_client:
        try:
            await (
                firestore_client.collection("sessions")
                .document(session_id)
                .collection("notes")
                .document(note_id)
                .set(
                    {
                        "title": title,
                        "content": content,
                        "note_type": "summary",
                        "status": "pending",
                        "student_id": runtime_state.get("student_id"),
                        "track_id": runtime_state.get("track_id"),
                        "topic_id": runtime_state.get("topic_id"),
                        "source": "auto_qa_note",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            )
        except Exception:
            logger.warning(
                "Session %s: failed to persist auto question note",
                session_id,
                exc_info=True,
            )

    runtime_state["pending_study_question"] = None
    logger.info("Session %s: auto My note saved from student question", session_id)


async def _maybe_store_example_note(
    session_id: str,
    runtime_state: dict,
    turn_text: str,
    wb_queue: asyncio.Queue | None,
    firestore_client: Any | None,
    report: Any | None = None,
) -> None:
    example_text = extract_example_from_turn(turn_text)
    if not example_text:
        return

    signature = _example_signature(example_text)
    signatures = runtime_state.setdefault("example_note_signatures", set())
    if signature in signatures:
        return
    signatures.add(signature)
    if len(signatures) > 200:
        signatures.clear()
        signatures.add(signature)

    sequence = int(runtime_state.get("example_note_counter", 0)) + 1
    runtime_state["example_note_counter"] = sequence
    title, content = build_example_note(example_text, sequence)
    title = normalize_title(title)
    content = normalize_content(content)
    now = time.time()
    note_id = f"note-{int(now * 1000)}-ex-{sequence}"
    note = {
        "id": note_id,
        "title": title,
        "content": content,
        "note_type": "summary",
        "status": "pending",
    }

    if wb_queue is not None:
        wb_queue.put_nowait(note)
        if report:
            report.record_whiteboard_note_created()
            report.record_whiteboard_note_queued(str(note_id))

    if firestore_client:
        try:
            await (
                firestore_client.collection("sessions")
                .document(session_id)
                .collection("notes")
                .document(note_id)
                .set(
                    {
                        "title": title,
                        "content": content,
                        "note_type": "summary",
                        "status": "pending",
                        "student_id": runtime_state.get("student_id"),
                        "track_id": runtime_state.get("track_id"),
                        "topic_id": runtime_state.get("topic_id"),
                        "source": "auto_example_note",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            )
        except Exception:
            logger.warning(
                "Session %s: failed to persist auto example note",
                session_id,
                exc_info=True,
            )

    logger.info("Session %s: auto My note saved from tutor example", session_id)
