"""Integration tests for tool functions in agent.py."""
import asyncio
from unittest.mock import patch

import pytest

from agent import write_notes, update_note_status, switch_topic, flag_drift
from modules.whiteboard import normalize_title


def _patch_infra(wb_queue=None):
    """Return a stack of patches that disable Firestore, reports, and inject queues."""
    return (
        patch("agent.get_firestore_client", return_value=None),
        patch("agent.get_report", return_value=None),
        patch("queues.get_whiteboard_queue", return_value=wb_queue),
    )


# -----------------------------------------------------------------------
# write_notes
# -----------------------------------------------------------------------
class TestWriteNotes:
    @pytest.mark.asyncio
    async def test_basic_note_creation(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await write_notes(
                title="Step 1",
                content="Add the fractions",
                note_type="checklist_item",
                status="pending",
                tool_context=tool_context,
            )

        assert result["result"] == "displayed"
        assert result["note_type"] == "checklist_item"
        assert result["status"] == "pending"
        assert result["note_id"].startswith("note-")

        queued = wb_queue.get_nowait()
        assert queued["title"] == normalize_title("Step 1")
        assert queued["note_type"] == "checklist_item"

    @pytest.mark.asyncio
    async def test_title_dedupe_rejects_duplicate(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            r1 = await write_notes("Step 1", "Content A", tool_context=tool_context)
            r2 = await write_notes("Step 1", "Content B", tool_context=tool_context)

        assert r1["result"] == "displayed"
        assert r2["result"] == "already_exists"
        assert r2["note_id"] == r1["note_id"]

    @pytest.mark.asyncio
    async def test_title_dedupe_case_insensitive(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            r1 = await write_notes("Step One", "Content", tool_context=tool_context)
            r2 = await write_notes("step one", "Different", tool_context=tool_context)

        assert r1["result"] == "displayed"
        assert r2["result"] == "already_exists"

    @pytest.mark.asyncio
    async def test_previous_notes_seeded_into_dedupe(self, tool_context, wb_queue):
        tool_context.state["previous_notes"] = [
            {"id": "old-note-1", "title": "Existing Note"},
        ]
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await write_notes("Existing Note", "New content", tool_context=tool_context)

        assert result["result"] == "already_exists"

    @pytest.mark.asyncio
    async def test_note_appended_to_previous_notes(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            await write_notes("New Note", "Content", tool_context=tool_context)

        assert len(tool_context.state["previous_notes"]) == 1
        assert tool_context.state["previous_notes"][0]["title"] == normalize_title("New Note")

    @pytest.mark.asyncio
    async def test_invalid_status_defaults_to_pending(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await write_notes("Title", "Content", status="invalid", tool_context=tool_context)

        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_invalid_note_type_defaults_to_insight(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await write_notes("Title", "Content", note_type="bogus", tool_context=tool_context)

        assert result["note_type"] == "insight"

    @pytest.mark.asyncio
    async def test_no_queue_still_succeeds(self, tool_context):
        p_fs, p_rpt, p_wbq = _patch_infra(None)
        with p_fs, p_rpt, p_wbq:
            result = await write_notes("Title", "Content", tool_context=tool_context)

        assert result["result"] == "displayed"


# -----------------------------------------------------------------------
# update_note_status
# -----------------------------------------------------------------------
class TestUpdateNoteStatus:
    @pytest.mark.asyncio
    async def test_valid_status_update(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await update_note_status("note-123", "done", tool_context)

        assert result["result"] == "updated"
        assert result["status"] == "done"

        queued = wb_queue.get_nowait()
        assert queued["action"] == "update_status"
        assert queued["id"] == "note-123"
        assert queued["status"] == "done"

    @pytest.mark.asyncio
    async def test_invalid_status_rejected(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await update_note_status("note-123", "invalid", tool_context)

        assert result["result"] == "error"

    @pytest.mark.asyncio
    async def test_status_stripped_and_lowercased(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await update_note_status("note-123", "  Done  ", tool_context)

        assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_all_valid_statuses(self, tool_context, wb_queue):
        for status in ("pending", "in_progress", "done", "mastered", "struggling"):
            ctx = type(tool_context)(state=dict(tool_context.state))
            p_fs, p_rpt, p_wbq = _patch_infra(asyncio.Queue())
            with p_fs, p_rpt, p_wbq:
                result = await update_note_status("note-1", status, ctx)
            assert result["result"] == "updated", f"Failed for status '{status}'"

    @pytest.mark.asyncio
    async def test_redundant_same_status_returns_noop(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            r1 = await update_note_status("note-123", "in_progress", tool_context)
            r2 = await update_note_status("note-123", "in_progress", tool_context)

        assert r1["result"] == "updated"
        assert r2["result"] == "noop"
        # Only one message should have been queued
        assert wb_queue.get_nowait()["action"] == "update_status"
        assert wb_queue.empty()

    @pytest.mark.asyncio
    async def test_different_status_after_same_updates(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            r1 = await update_note_status("note-123", "in_progress", tool_context)
            r2 = await update_note_status("note-123", "in_progress", tool_context)
            r3 = await update_note_status("note-123", "done", tool_context)

        assert r1["result"] == "updated"
        assert r2["result"] == "noop"
        assert r3["result"] == "updated"


# -----------------------------------------------------------------------
# switch_topic
# -----------------------------------------------------------------------
class TestSwitchTopic:
    def _patch_switch(self, wb_queue, topic_queue):
        return (
            patch("agent.get_firestore_client", return_value=None),
            patch("agent.get_report", return_value=None),
            patch("queues.get_whiteboard_queue", return_value=wb_queue),
            patch("queues.get_topic_update_queue", return_value=topic_queue),
        )

    @pytest.mark.asyncio
    async def test_topic_switch_updates_state(self, tool_context, wb_queue):
        topic_queue = asyncio.Queue()
        p_fs, p_rpt, p_wbq, p_tq = self._patch_switch(wb_queue, topic_queue)
        with p_fs, p_rpt, p_wbq, p_tq:
            result = await switch_topic("topic-algebra", "Algebra", tool_context)

        assert result["result"] == "switched"
        assert tool_context.state["topic_id"] == "topic-algebra"
        assert tool_context.state["topic_title"] == "Algebra"
        assert tool_context.state["topic_status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_noop_if_same_topic(self, tool_context, wb_queue):
        tool_context.state["topic_id"] = "topic-fractions"
        topic_queue = asyncio.Queue()
        p_fs, p_rpt, p_wbq, p_tq = self._patch_switch(wb_queue, topic_queue)
        with p_fs, p_rpt, p_wbq, p_tq:
            result = await switch_topic("topic-fractions", "Fractions", tool_context)

        assert result["result"] == "noop"

    @pytest.mark.asyncio
    async def test_topic_update_queue_notified(self, tool_context, wb_queue):
        topic_queue = asyncio.Queue()
        p_fs, p_rpt, p_wbq, p_tq = self._patch_switch(wb_queue, topic_queue)
        with p_fs, p_rpt, p_wbq, p_tq:
            await switch_topic("topic-algebra", "Algebra", tool_context)

        update = topic_queue.get_nowait()
        assert update["topic_id"] == "topic-algebra"
        assert update["topic_title"] == "Algebra"

    @pytest.mark.asyncio
    async def test_dedupe_state_reset_on_switch(self, tool_context, wb_queue):
        """switch_topic clears previous_notes and _session_note_titles."""
        tool_context.state["previous_notes"] = [
            {"id": "old-note", "title": "Old Note"},
        ]
        tool_context.state["_session_note_titles"] = {"old note": "old-note"}

        topic_queue = asyncio.Queue()
        p_fs, p_rpt, p_wbq, p_tq = self._patch_switch(wb_queue, topic_queue)
        with p_fs, p_rpt, p_wbq, p_tq:
            await switch_topic("topic-algebra", "Algebra", tool_context)

        assert tool_context.state["previous_notes"] == []
        assert tool_context.state["_session_note_titles"] == {}

    @pytest.mark.asyncio
    async def test_clear_dedupe_action_sent_to_wb_queue(self, tool_context, wb_queue):
        """switch_topic sends clear_dedupe action to wb_queue."""
        topic_queue = asyncio.Queue()
        p_fs, p_rpt, p_wbq, p_tq = self._patch_switch(wb_queue, topic_queue)
        with p_fs, p_rpt, p_wbq, p_tq:
            await switch_topic("topic-algebra", "Algebra", tool_context)

        action = wb_queue.get_nowait()
        assert action == {"action": "clear_dedupe"}

    @pytest.mark.asyncio
    async def test_no_dead_update_topic_action(self, tool_context, wb_queue):
        """switch_topic no longer sends update_topic action to wb_queue."""
        topic_queue = asyncio.Queue()
        p_fs, p_rpt, p_wbq, p_tq = self._patch_switch(wb_queue, topic_queue)
        with p_fs, p_rpt, p_wbq, p_tq:
            await switch_topic("topic-algebra", "Algebra", tool_context)

        messages = []
        while not wb_queue.empty():
            messages.append(wb_queue.get_nowait())
        all_actions = [m.get("action") for m in messages if "action" in m]
        assert "update_topic" not in all_actions

    @pytest.mark.asyncio
    async def test_write_notes_works_after_switch(self, tool_context, wb_queue):
        """After switching topics, a note with same title as old topic note succeeds."""
        # Create note on old topic
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            r1 = await write_notes("Step 1", "Old content", tool_context=tool_context)
        assert r1["result"] == "displayed"

        # Switch topic
        topic_queue = asyncio.Queue()
        p_fs2, p_rpt2, p_wbq2, p_tq = (
            patch("agent.get_firestore_client", return_value=None),
            patch("agent.get_report", return_value=None),
            patch("queues.get_whiteboard_queue", return_value=wb_queue),
            patch("queues.get_topic_update_queue", return_value=topic_queue),
        )
        with p_fs2, p_rpt2, p_wbq2, p_tq:
            await switch_topic("topic-algebra", "Algebra", tool_context)

        # Drain the clear_dedupe from wb_queue
        while not wb_queue.empty():
            wb_queue.get_nowait()

        # Same title on new topic — should succeed, not be deduped
        p_fs3, p_rpt3, p_wbq3 = _patch_infra(wb_queue)
        with p_fs3, p_rpt3, p_wbq3:
            r2 = await write_notes("Step 1", "New content", tool_context=tool_context)
        assert r2["result"] == "displayed"


# -----------------------------------------------------------------------
# flag_drift
# -----------------------------------------------------------------------
class TestFlagDrift:
    @pytest.mark.asyncio
    async def test_basic_drift_flagged(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await flag_drift("off_topic", "asked about weather", tool_context)

        assert result["result"] == "flagged"
        assert result["drift_type"] == "off_topic"
        assert result["reason"] == "asked about weather"

        queued = wb_queue.get_nowait()
        assert queued["action"] == "guardrail_event"
        assert queued["drift_type"] == "off_topic"

    @pytest.mark.asyncio
    async def test_cheat_request_type(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await flag_drift("cheat_request", "asked for answer", tool_context)

        assert result["drift_type"] == "cheat_request"

    @pytest.mark.asyncio
    async def test_invalid_type_defaults_to_off_topic(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await flag_drift("bogus", "some reason", tool_context)

        assert result["drift_type"] == "off_topic"

    @pytest.mark.asyncio
    async def test_no_queue_still_succeeds(self, tool_context):
        p_fs, p_rpt, p_wbq = _patch_infra(None)
        with p_fs, p_rpt, p_wbq:
            result = await flag_drift("off_topic", "test", tool_context)

        assert result["result"] == "flagged"

    @pytest.mark.asyncio
    async def test_empty_reason_accepted(self, tool_context, wb_queue):
        p_fs, p_rpt, p_wbq = _patch_infra(wb_queue)
        with p_fs, p_rpt, p_wbq:
            result = await flag_drift("off_topic", "", tool_context)

        assert result["result"] == "flagged"
        assert result["reason"] == ""
