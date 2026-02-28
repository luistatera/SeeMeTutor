"""Unit tests for modules/whiteboard.py — normalization, dedupe, dispatcher."""
import asyncio
import json
import time

import pytest

from modules.whiteboard import (
    VALID_NOTE_TYPES,
    VALID_NOTE_STATUSES,
    NOTE_MAX_CHARS,
    NOTE_MAX_LINES,
    NOTE_TITLE_MAX_CHARS,
    WHITEBOARD_SYNC_WAIT_S,
    normalize_note_type,
    normalize_title,
    normalize_content,
    dedupe_key,
    init_whiteboard_state,
    whiteboard_dispatcher,
    _inline_sentences_to_bullets,
)


# -----------------------------------------------------------------------
# normalize_note_type
# -----------------------------------------------------------------------
class TestNormalizeNoteType:
    @pytest.mark.parametrize("value", sorted(VALID_NOTE_TYPES))
    def test_valid_types_pass_through(self, value):
        assert normalize_note_type(value) == value

    def test_strips_whitespace_and_lowercases(self):
        assert normalize_note_type("  Formula  ") == "formula"

    def test_invalid_returns_insight(self):
        assert normalize_note_type("unknown_type") == "insight"

    def test_none_returns_insight(self):
        assert normalize_note_type(None) == "insight"

    def test_empty_string_returns_insight(self):
        assert normalize_note_type("") == "insight"


# -----------------------------------------------------------------------
# normalize_title
# -----------------------------------------------------------------------
class TestNormalizeTitle:
    def test_strips_whitespace(self):
        assert normalize_title("  Hello  ") == "Hello"

    def test_empty_returns_default(self):
        assert normalize_title("") == "Current Step"

    def test_none_returns_default(self):
        assert normalize_title(None) == "Current Step"

    def test_truncates_long_title(self):
        long_title = "A" * 100
        result = normalize_title(long_title)
        assert len(result) < len(long_title)
        assert result.endswith("...")

    def test_exact_length_not_truncated(self):
        exact = "A" * NOTE_TITLE_MAX_CHARS
        result = normalize_title(exact)
        assert result == exact


# -----------------------------------------------------------------------
# normalize_content
# -----------------------------------------------------------------------
class TestNormalizeContent:
    def test_empty_returns_default(self):
        assert normalize_content("") == "- Review this step carefully."

    def test_none_returns_default(self):
        assert normalize_content(None) == "- Review this step carefully."

    def test_short_text_gets_bullet(self):
        result = normalize_content("Check your work")
        assert result.startswith("- ")

    def test_structured_text_keeps_bullets(self):
        text = "- Step one\n- Step two"
        result = normalize_content(text)
        assert "- Step one" in result
        assert "- Step two" in result

    def test_formula_line_not_bulleted(self):
        text = "a = b + c"
        result = normalize_content(text)
        assert not result.startswith("- ")

    def test_arrow_line_not_bulleted(self):
        text = "input -> output"
        result = normalize_content(text)
        assert not result.startswith("- ")

    def test_max_lines_enforced(self):
        lines = "\n".join(f"Line {i}" for i in range(20))
        result = normalize_content(lines)
        assert result.count("\n") < NOTE_MAX_LINES

    def test_max_chars_enforced(self):
        text = "\n".join("A" * 80 for _ in range(10))
        result = normalize_content(text)
        assert len(result) < len(text)
        assert result.endswith("...")

    def test_long_inline_text_split_to_bullets(self):
        text = "First sentence. Second sentence. Third sentence. " * 4
        result = normalize_content(text)
        assert "\n" in result

    def test_crlf_normalized(self):
        text = "Line one\r\nLine two\rLine three"
        result = normalize_content(text)
        assert "\r" not in result

    def test_blank_lines_removed(self):
        text = "Line one\n\n\nLine two"
        result = normalize_content(text)
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) == 2

    def test_numbered_list_preserved(self):
        text = "1. First step\n2. Second step"
        result = normalize_content(text)
        assert "1." in result
        assert "2." in result


# -----------------------------------------------------------------------
# _inline_sentences_to_bullets
# -----------------------------------------------------------------------
class TestInlineSentencesToBullets:
    def test_single_sentence_unchanged(self):
        assert _inline_sentences_to_bullets("Hello world") == "Hello world"

    def test_multiple_sentences_become_bullets(self):
        text = "First. Second. Third."
        result = _inline_sentences_to_bullets(text)
        assert result.startswith("- First.")
        assert "- Second." in result

    def test_max_lines_respected(self):
        text = ". ".join(f"Sentence {i}" for i in range(20)) + "."
        result = _inline_sentences_to_bullets(text)
        assert result.count("\n") < NOTE_MAX_LINES


# -----------------------------------------------------------------------
# dedupe_key
# -----------------------------------------------------------------------
class TestDedupeKey:
    def test_basic_key(self):
        key = dedupe_key("My Title", "My Content")
        assert key == "my title||my content"

    def test_normalizes_whitespace(self):
        key1 = dedupe_key("  My  Title  ", "  My  Content  ")
        key2 = dedupe_key("My Title", "My Content")
        assert key1 == key2

    def test_case_insensitive(self):
        key1 = dedupe_key("HELLO", "WORLD")
        key2 = dedupe_key("hello", "world")
        assert key1 == key2

    def test_different_content_different_keys(self):
        key1 = dedupe_key("Title", "Content A")
        key2 = dedupe_key("Title", "Content B")
        assert key1 != key2

    def test_different_title_different_keys(self):
        key1 = dedupe_key("Title A", "Content")
        key2 = dedupe_key("Title B", "Content")
        assert key1 != key2


# -----------------------------------------------------------------------
# init_whiteboard_state
# -----------------------------------------------------------------------
class TestInitWhiteboardState:
    def test_returns_expected_keys(self):
        state = init_whiteboard_state()
        assert isinstance(state["wb_dedupe_keys"], set)
        assert len(state["wb_dedupe_keys"]) == 0
        assert state["wb_notes_queued"] == 0
        assert state["wb_notes_sent"] == 0
        assert state["wb_notes_deduped"] == 0
        assert state["wb_while_speaking"] == 0
        assert state["wb_outside_speaking"] == 0


# -----------------------------------------------------------------------
# whiteboard_dispatcher
# -----------------------------------------------------------------------
class TestWhiteboardDispatcher:
    @pytest.mark.asyncio
    async def test_action_clear_forwarded(self, fake_ws, wb_queue, runtime_state):
        """Clear action is forwarded to browser."""
        wb_queue.put_nowait({"action": "clear"})

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) == 1
        assert sent[0]["data"]["action"] == "clear"

    @pytest.mark.asyncio
    async def test_action_update_status_forwarded(self, fake_ws, wb_queue, runtime_state):
        """update_status action is forwarded to browser."""
        wb_queue.put_nowait({"action": "update_status", "id": "note-1", "status": "done"})

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) == 1
        assert sent[0]["data"]["action"] == "update_status"

    @pytest.mark.asyncio
    async def test_clear_dedupe_resets_keys_not_forwarded(self, fake_ws, wb_queue, runtime_state):
        """clear_dedupe resets wb_dedupe_keys and is NOT sent to browser."""
        runtime_state["wb_dedupe_keys"].add("some||key")
        wb_queue.put_nowait({"action": "clear_dedupe"})

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(runtime_state["wb_dedupe_keys"]) == 0
        assert len(fake_ws.get_sent_json()) == 0  # nothing sent to browser

    @pytest.mark.asyncio
    async def test_guardrail_event_forwarded_as_own_type(self, fake_ws, wb_queue, runtime_state):
        """guardrail_event action is sent as type='guardrail_event', not 'whiteboard'."""
        wb_queue.put_nowait({
            "action": "guardrail_event",
            "drift_type": "off_topic",
            "reason": "asked about weather",
        })

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) == 1
        assert sent[0]["type"] == "guardrail_event"
        assert sent[0]["data"]["type"] == "off_topic"
        assert sent[0]["data"]["source"] == "model_drift"
        assert sent[0]["data"]["detail"] == "asked about weather"

    @pytest.mark.asyncio
    async def test_note_dispatched_on_deadline(self, fake_ws, wb_queue, runtime_state):
        """Notes dispatch after deadline even if not speaking."""
        wb_queue.put_nowait({
            "title": "Test Note",
            "content": "- Step 1",
            "note_type": "insight",
        })

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(WHITEBOARD_SYNC_WAIT_S + 0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) >= 1
        assert sent[0]["type"] == "whiteboard"
        assert "_queued_at_ms" not in sent[0]["data"]
        assert "_dispatch_deadline_ms" not in sent[0]["data"]

    @pytest.mark.asyncio
    async def test_note_dispatched_while_speaking(self, fake_ws, wb_queue, runtime_state):
        """Notes dispatch immediately when assistant is speaking."""
        runtime_state["assistant_speaking"] = True
        wb_queue.put_nowait({
            "title": "Speaking Note",
            "content": "- During speech",
            "note_type": "insight",
        })

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) >= 1
        assert runtime_state["wb_while_speaking"] >= 1

    @pytest.mark.asyncio
    async def test_content_dedupe_skips_duplicate(self, fake_ws, wb_queue, runtime_state):
        """Second note with same title+content is deduped."""
        note = {"title": "Same Title", "content": "- Same content", "note_type": "insight"}
        wb_queue.put_nowait(dict(note))
        wb_queue.put_nowait(dict(note))

        runtime_state["assistant_speaking"] = True
        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) == 1
        assert runtime_state["wb_notes_deduped"] == 1

    @pytest.mark.asyncio
    async def test_clear_dedupe_allows_re_creation(self, fake_ws, wb_queue, runtime_state):
        """After clear_dedupe, same note can be created again."""
        runtime_state["assistant_speaking"] = True
        note = {"title": "Note A", "content": "- Content", "note_type": "insight"}

        # First note
        wb_queue.put_nowait(dict(note))
        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)

        # Reset dedupe
        wb_queue.put_nowait({"action": "clear_dedupe"})
        await asyncio.sleep(0.1)

        # Same note again — should NOT be deduped
        wb_queue.put_nowait(dict(note))
        await asyncio.sleep(0.15)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        whiteboard_notes = [s for s in fake_ws.get_sent_json() if s.get("type") == "whiteboard"]
        assert len(whiteboard_notes) == 2

    @pytest.mark.asyncio
    async def test_flush_on_cancel(self, fake_ws, wb_queue, runtime_state):
        """Pending notes are flushed when dispatcher is cancelled."""
        wb_queue.put_nowait({"title": "Pending", "content": "- Will flush", "note_type": "insight"})

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        sent = fake_ws.get_sent_json()
        assert len(sent) >= 1

    @pytest.mark.asyncio
    async def test_counters_increment(self, fake_ws, wb_queue, runtime_state):
        """Dispatch increments wb_notes_queued and wb_notes_sent."""
        runtime_state["assistant_speaking"] = True
        wb_queue.put_nowait({"title": "Note 1", "content": "- A", "note_type": "insight"})
        wb_queue.put_nowait({"title": "Note 2", "content": "- B", "note_type": "formula"})

        task = asyncio.create_task(whiteboard_dispatcher(fake_ws, wb_queue, runtime_state))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert runtime_state["wb_notes_queued"] == 2
        assert runtime_state["wb_notes_sent"] == 2
