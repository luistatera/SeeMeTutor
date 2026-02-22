import asyncio
import os
from google import genai
from google.genai import types

async def main():
    client = genai.Client()
    print("Testing connection WITH google_search tool...")
    try:
        async with client.aio.live.connect(
            model="gemini-2.5-flash",
            config=types.LiveConnectConfig(
                tools=[{"google_search": {}}],
            )
        ) as session:
            print("Connected successfully!")
            await session.send(input=types.LiveClientContent(
                turns=[types.Content(role="user", parts=[types.Part.from_text("Hello")])],
            ))
            async for response in session.receive():
                print(response)
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
