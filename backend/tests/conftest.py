"""Shared test fixtures for whiteboard and tool tests."""
import asyncio
import json

import pytest

from modules.whiteboard import init_whiteboard_state


class FakeToolContext:
    """Minimal ToolContext mock for testing tool functions.

    ADK's ToolContext provides a .state dict-like attribute that tools
    use to read/write session state. This fake mirrors that interface.
    """

    def __init__(self, state: dict | None = None):
        self.state = state or {}


class FakeWebSocket:
    """Captures send_text calls for assertion."""

    def __init__(self):
        self.sent: list[str] = []
        self._closed = False

    async def send_text(self, data: str):
        if self._closed:
            raise RuntimeError("WebSocket closed")
        self.sent.append(data)

    def close(self):
        self._closed = True

    def get_sent_json(self) -> list[dict]:
        return [json.loads(s) for s in self.sent]


@pytest.fixture
def tool_context():
    """Return a fresh FakeToolContext with typical session state."""
    return FakeToolContext(state={
        "session_id": "test-session-001",
        "student_id": "student-001",
        "track_id": "track-math",
        "topic_id": "topic-fractions",
        "topic_title": "Fractions",
        "topic_status": "in_progress",
        "session_phase": "tutoring",
        "previous_notes": [],
        "_session_note_titles": {},
    })


@pytest.fixture
def wb_queue():
    """Return a fresh asyncio.Queue for whiteboard messages."""
    return asyncio.Queue()


@pytest.fixture
def runtime_state():
    """Return a fresh runtime_state dict with whiteboard keys."""
    state = {
        "assistant_speaking": False,
        "last_user_activity_at": 0.0,
    }
    state.update(init_whiteboard_state())
    return state


@pytest.fixture
def fake_ws():
    """Return a FakeWebSocket for capturing dispatcher output."""
    return FakeWebSocket()
