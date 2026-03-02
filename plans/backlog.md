# Backlog

## Proposed Feature: YouTube Transcript Explanation

-----

The product have to have a pedagogical layer

-----

Some students want to be able to share the link of a video and ask the tutor to explain it to them.
Questions:

- How students can share the URL of the video with the tutor?

## Proposed Feature: YouTube Transcript Explanation

To allow students to learn from video content without having to watch it via real-time screen share, we will implement a YouTube transcript extraction tool.

**Implementation Plan:**

1. **Backend Tool Integration:**
   - Add `youtube-transcript-api` to `requirements.txt`.
   - Create a new ADK Tool `get_video_transcript(video_url: str)` in the backend.
   - The tool will extract the video ID, fetch the transcript in supported languages (EN, PT, DE), and return the combined text string.

2. **Frontend Update:**
   - Add a simple text input box in the frontend: *"Paste a YouTube link here."*
   - When a link is submitted, send a WebSocket message to the backend.

3. **Agent Orchestration (Internal Control):**
   - The backend catches the URL and issues an internal control instruction to Gemini: `"INTERNAL CONTROL: The student just shared this video URL: {url}. Use your tools to read it and ask them what they want to know."`
   - The Coordinator agent uses the `get_video_transcript` tool to ingest the text instantly (< 1 second).
   - The tutor proactively guides the student based on the video content.

This avoids the 10-15 second upload penalty of the Gemini File API and easily respects the < 500ms latency budget.

-----

When session ends, collect the user progress, struggle, what the user did correctly (strenghts) and send it to user's email.

----

DEMO:

This is the style and lenght of the demo I want to have for Milo Tutor.ai
<https://www.youtube.com/watch?v=4mnP1lRdUm8>

-----
