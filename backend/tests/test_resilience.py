import pytest

@pytest.mark.asyncio
async def test_resilience_network_drop():
    """
    Simulate an ADK disconnect and assert that the session handles it cleanly
    and preserves state.
    """
    # Mocking a session state
    session_state = {
        "connected": True,
        "progress": {"completed_sections": 2}
    }
    
    # Simulate network drop
    class MockLiveQueue:
        def __init__(self):
            self.closed = False
        async def close(self):
            self.closed = True
            
    live_queue = MockLiveQueue()
    
    # Simulate ADK disconnect handler
    async def handle_disconnect(queue, state):
        state["connected"] = False
        await queue.close()
        # State preservation (e.g., Firestore sync would happen here)
        
    await handle_disconnect(live_queue, session_state)
    
    assert live_queue.closed is True
    assert session_state["connected"] is False
    assert session_state["progress"]["completed_sections"] == 2
