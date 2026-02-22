import asyncio
import os
import sys

# Make sure monkey-patch applies
import gemini_live

from google.adk.agents import Agent
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue, LiveRequest
from google.genai import types

async def test_live():
    agent = Agent(
        name="test_agent",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        instruction="You are a helpful assistant.",
    )
    
    from google.adk.runners import Runner
    runner = Runner(agent)
    queue = LiveRequestQueue()
    
    config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
    )
    
    try:
        print("Starting live session...")
        agen = runner.run_live(
            user_id="test_user",
            session_id="test_session",
            live_request_queue=queue,
            run_config=config,
        )
        
        # Send text to trigger client content
        print("Sending text...")
        queue.send_content(types.Content(role="user", parts=[types.Part(text="Hello!")]))
        
        # Read a few responses
        async for event in agen:
            print("Event:", event)
            break
            
    except Exception as e:
        print("ERROR Caught:", type(e), e)
    finally:
        queue.close()

if __name__ == "__main__":
    if "GEMINI_API_KEY" not in os.environ:
        print("Please set GEMINI_API_KEY")
        sys.exit(1)
    asyncio.run(test_live())
