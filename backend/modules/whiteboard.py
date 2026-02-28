"""
Whiteboard sync module — note normalization, deduplication, and dispatch.

Normalizes LLM-generated note content for consistent whiteboard rendering,
deduplicates notes by content similarity, and dispatches notes in sync
with tutor speech for a coordinated teaching experience.
"""

import asyncio
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
NOTE_MAX_LINES = 6
NOTE_MAX_CHARS = 460
NOTE_TITLE_MAX_CHARS = 72
WHITEBOARD_SYNC_WAIT_S = 0.5       # Max wait for speaking window before deadline dispatch
WHITEBOARD_DISPATCH_POLL_S = 0.05   # How often the dispatcher checks for ready notes

VALID_NOTE_TYPES = {"insight", "checklist_item", "formula", "summary", "vocabulary"}
VALID_NOTE_STATUSES = {"pending", "in_progress", "done", "mastered", "struggling"}

_SPACES_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
def normalize_note_type(value: str) -> str:
    """Normalize note_type to a valid enum value, defaulting to 'insight'."""
    cleaned = str(value or "").strip().lower()
    if cleaned not in VALID_NOTE_TYPES:
        return "insight"
    return cleaned


def normalize_title(title: str) -> str:
    """Clean and truncate note title."""
    cleaned = str(title or "").strip()
    if not cleaned:
        return "Current Step"
    if len(cleaned) > NOTE_TITLE_MAX_CHARS:
        cleaned = cleaned[:NOTE_TITLE_MAX_CHARS - 1].rstrip() + "..."
    return cleaned


def _inline_sentences_to_bullets(text: str) -> str:
    """Split a long inline paragraph into bullet points by sentence."""
    sentence_parts = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", text)
        if part.strip()
    ]
    if len(sentence_parts) <= 1:
        return text
    sentence_parts = sentence_parts[:NOTE_MAX_LINES]
    return "\n".join(f"- {part}" for part in sentence_parts)


def normalize_content(content: str) -> str:
    """Clean, structure, and truncate note content for whiteboard display."""
    raw = str(content or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return "- Review this step carefully."

    # Long inline text → bullet points
    if "\n" not in raw and len(raw) > 170:
        raw = _inline_sentences_to_bullets(raw)

    normalized_lines: list[str] = []
    for line in raw.split("\n"):
        clean_line = _SPACES_RE.sub(" ", line).strip()
        if not clean_line:
            continue

        # Truncate very long lines that aren't already structured
        if len(clean_line) > 160 and not re.match(r"^[-*\d]", clean_line):
            clean_line = clean_line[:157].rstrip() + "..."

        normalized_lines.append(clean_line)
        if len(normalized_lines) >= NOTE_MAX_LINES:
            break

    if not normalized_lines:
        normalized_lines = ["- Review this step carefully."]

    # Auto-bullet if content has no structure
    has_structured_line = any(
        re.match(r"^([-*]|\d+\.|[A-Za-z]\))\s+", line) for line in normalized_lines
    )
    has_formula_line = any(
        ("=" in line or "->" in line or "=>" in line) for line in normalized_lines
    )
    if not has_structured_line and not has_formula_line:
        normalized_lines = [f"- {line}" for line in normalized_lines]

    content_out = "\n".join(normalized_lines)
    if len(content_out) > NOTE_MAX_CHARS:
        content_out = content_out[:NOTE_MAX_CHARS - 1].rstrip() + "..."

    return content_out


def dedupe_key(title: str, content: str) -> str:
    """Create a normalized key for content-based deduplication."""
    t = re.sub(r"\s+", " ", title.strip().lower())
    c = re.sub(r"\s+", " ", content.strip().lower())
    return f"{t}||{c}"


# ---------------------------------------------------------------------------
# Whiteboard state
# ---------------------------------------------------------------------------
def init_whiteboard_state() -> dict:
    """Return initial whiteboard-specific keys to merge into runtime_state."""
    return {
        "wb_dedupe_keys": set(),
        "wb_notes_queued": 0,
        "wb_notes_sent": 0,
        "wb_notes_deduped": 0,
        "wb_while_speaking": 0,
        "wb_outside_speaking": 0,
    }


# ---------------------------------------------------------------------------
# Whiteboard dispatcher — async task that sends notes in sync with speech
# ---------------------------------------------------------------------------
async def whiteboard_dispatcher(
    websocket,
    wb_queue: asyncio.Queue,
    runtime_state: dict,
) -> None:
    """Dispatch whiteboard notes to the browser, preferring speech-sync timing.

    Notes are held briefly (up to WHITEBOARD_SYNC_WAIT_S) and dispatched when:
    - The tutor is currently speaking (synchronized delivery), OR
    - The deadline expires (deadline fallback — ensures notes are never lost).

    Special action messages (clear, update_status) are forwarded immediately
    without sync delay. The clear_dedupe action resets the content-based
    dedupe set (used on topic switch) without sending anything to the browser.
    """
    pending: list[dict] = []

    try:
        while True:
            # Pull newly queued items
            while True:
                try:
                    item = wb_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Action messages pass through immediately
                if "action" in item:
                    action = item["action"]
                    if action == "clear_dedupe":
                        runtime_state["wb_dedupe_keys"] = set()
                        logger.info("Whiteboard dedupe keys reset (topic switch)")
                    elif action == "guardrail_event":
                        await _send_ws(websocket, {
                            "type": "guardrail_event",
                            "data": {
                                "type": item.get("drift_type", "drift"),
                                "source": "model_drift",
                                "detail": item.get("reason", ""),
                            },
                        })
                    else:
                        await _send_ws(websocket, {"type": "whiteboard", "data": item})
                    continue

                # Notes arrive pre-normalized from write_notes tool

                # Content-based dedupe
                key = dedupe_key(item["title"], item["content"])
                dedupe_keys = runtime_state.get("wb_dedupe_keys", set())
                if key in dedupe_keys:
                    runtime_state["wb_notes_deduped"] = runtime_state.get("wb_notes_deduped", 0) + 1
                    logger.info(
                        "Whiteboard content dedupe: skipped '%s' (total deduped=%d)",
                        item.get("title", "")[:40],
                        runtime_state["wb_notes_deduped"],
                    )
                    rpt = runtime_state.get("_report")
                    if rpt:
                        rpt.record_whiteboard_duplicate_skipped()
                    continue
                dedupe_keys.add(key)

                # Stamp dispatch deadline
                now_ms = int(time.time() * 1000)
                item["_queued_at_ms"] = now_ms
                item["_dispatch_deadline_ms"] = now_ms + int(WHITEBOARD_SYNC_WAIT_S * 1000)
                pending.append(item)
                runtime_state["wb_notes_queued"] = runtime_state.get("wb_notes_queued", 0) + 1

            if pending:
                now_ms = int(time.time() * 1000)
                speaking = bool(runtime_state.get("assistant_speaking"))
                ready: list[dict] = []
                deferred: list[dict] = []

                for note in pending:
                    deadline_reached = now_ms >= note.get("_dispatch_deadline_ms", now_ms)
                    if speaking or deadline_reached:
                        ready.append(note)
                    else:
                        deferred.append(note)

                pending = deferred

                for note in ready:
                    speaking_now = bool(runtime_state.get("assistant_speaking"))
                    if speaking_now:
                        runtime_state["wb_while_speaking"] = runtime_state.get("wb_while_speaking", 0) + 1
                    else:
                        runtime_state["wb_outside_speaking"] = runtime_state.get("wb_outside_speaking", 0) + 1

                    # Strip internal dispatcher fields before sending
                    payload = {k: v for k, v in note.items() if not k.startswith("_")}
                    await _send_ws(websocket, {"type": "whiteboard", "data": payload})
                    runtime_state["wb_notes_sent"] = runtime_state.get("wb_notes_sent", 0) + 1
                    rpt = runtime_state.get("_report")
                    if rpt:
                        sync_mode = "speech" if speaking_now else "deadline"
                        rpt.record_whiteboard_note_delivered(payload.get("id"), sync_mode)

                    logger.info(
                        "Whiteboard note sent: '%s' [sync=%s, sent=%d]",
                        note.get("title", "")[:40],
                        "speech" if speaking_now else "deadline",
                        runtime_state["wb_notes_sent"],
                    )

            await asyncio.sleep(WHITEBOARD_DISPATCH_POLL_S)

    except asyncio.CancelledError:
        # Flush any remaining pending notes before exit
        for note in pending:
            payload = {k: v for k, v in note.items() if not k.startswith("_")}
            await _send_ws(websocket, {"type": "whiteboard", "data": payload})
        logger.info("Whiteboard dispatcher stopped (flushed %d pending)", len(pending))
    except Exception as exc:
        logger.exception("Whiteboard dispatcher error: %s", exc)


async def _send_ws(websocket, payload: dict) -> None:
    """Send JSON to browser, ignoring closed-socket errors."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception:
        pass
