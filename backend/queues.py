"""
Queue registries for inter-component communication.

Whiteboard and topic-update queues allow tool functions (agent.py) to push
data to the correct WebSocket client without circular imports.
"""

import asyncio

# ---------------------------------------------------------------------------
# Whiteboard queue registry
# ---------------------------------------------------------------------------
_whiteboard_queues: dict[str, asyncio.Queue] = {}


def register_whiteboard_queue(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _whiteboard_queues[session_id] = q
    return q


def get_whiteboard_queue(session_id: str) -> asyncio.Queue | None:
    return _whiteboard_queues.get(session_id)


def unregister_whiteboard_queue(session_id: str) -> None:
    _whiteboard_queues.pop(session_id, None)


# ---------------------------------------------------------------------------
# Topic-update queue registry
# ---------------------------------------------------------------------------
_topic_update_queues: dict[str, asyncio.Queue] = {}


def register_topic_update_queue(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _topic_update_queues[session_id] = q
    return q


def get_topic_update_queue(session_id: str) -> asyncio.Queue | None:
    return _topic_update_queues.get(session_id)


def unregister_topic_update_queue(session_id: str) -> None:
    _topic_update_queues.pop(session_id, None)
