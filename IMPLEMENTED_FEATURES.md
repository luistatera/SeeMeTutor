# SeeMe Tutor — Implemented Features

This document provides a central, up-to-date catalog of all features currently implemented and live in the SeeMe Tutor codebase.

Last updated: February 22, 2026

---

## 1. Core Architecture & Infrastructure

* **FastAPI Backend Engine**: A high-performance Python backend serving both static assets and managing live API connections.
* **WebSocket Bridge**: Full-duplex WebSocket connection (`/ws?student_id=...&code=...`) bridging the browser's WebRTC media streams and the backend logic.
* **ADK Agent Integration**: The tutor is defined as an ADK `Agent` (`tutor_agent/agent.py`) with a comprehensive system prompt and 5 registered tools. The `ADKLiveSession` wrapper (`gemini_live.py`) manages bidirectional streaming via the ADK `Runner` with `LiveRequestQueue`.
* **Cloud Run & Firebase Ready**: Backend health checks (`/health`) and structured deployment config for GCP Cloud Run. Frontend set up as a PWA for Firebase Hosting.
* **Firestore Session & Learning Analytics**: Full Firestore integration for students, learning tracks, topics, progress milestones, struggle checkpoints, voice command telemetry, and session metadata.
* **Demo Security Gate**: WebSocket connections validate a `DEMO_ACCESS_CODE` environment variable to prevent unauthorized usage and control API costs.
* **CI/CD Pipeline**: GitHub Actions workflow deploys to Cloud Run and Firebase on push to `main`.

## 2. Real-Time Media & AI Behavior

* **Continuous Audio Streaming**: The client captures 16kHz PCM audio via `ScriptProcessorNode` and streams it as base64 to the backend, which forwards it to Gemini via ADK.
* **Vision/Camera Grounding**: The client captures JPEG frames from the user's camera at 1 FPS and streams them. The Gemini model uses this to "see" the homework and provide proactive visual assistance.
* **Barge-in / Interruption Handling**: True interruption functionality where the student can cut off the tutor mid-sentence. The system detects ADK `interrupted` events and immediately halts all scheduled audio playback.
* **Socratic & Multilingual Tutoring**: Per-student language contracts (immersion, guided bilingual, auto) govern language behavior. The tutor follows the Socratic method with LearnLM-informed pedagogy: active learning, cognitive load management, curiosity stimulation, and metacognitive development.
* **Whiteboard Notes**: The tutor proactively calls the `write_notes` tool to push formatted note cards (title + content) to the student's screen — formulas, step-by-step outlines, vocabulary lists, or summaries. Notes flow through an async queue from the ADK tool to the WebSocket client.

## 3. ADK Agent Tools

* **`get_backlog_context`**: Returns the active student's profile, learning track, current topic, language policy, and language contract from session state.
* **`log_progress`**: Records learning milestones (mastered/struggling/improving) to Firestore. Triggers struggle checkpoints when a topic reaches 2+ struggles.
* **`set_checkpoint_decision`**: Persists the student's choice (now/later/resolved) for a struggle checkpoint, updating topic and checkpoint status in Firestore.
* **`write_notes`**: Pushes a note card to the student's whiteboard via the whiteboard queue registry.
* **`google_search`**: ADK-provided Google Search tool, restricted to explicit student requests.

## 4. Frontend User Experience (Meeting-Style PWA)

* **Meeting-Style Portrait Layout**: Resembles a video call in portrait mode. Tutor tile at top, whiteboard as main content area, camera as a small PiP overlay, floating captions, and control bar at bottom. Max-width 600px for portrait/tablet optimization.
* **Tutor Tile**: Compact horizontal strip showing tutor avatar (with wave animation when speaking), name, status ("Ready" / "Listening" / "Speaking..."), and profile/track/topic pills.
* **Whiteboard**: Scrollable area where note cards from the tutor appear with animated entry. Shows an empty state message until the first note arrives.
* **Camera PiP**: 120x90px absolute-positioned overlay in bottom-right corner of the main area. Shows LIVE badge when active. Camera placeholder icon when off.
* **Floating Captions**: Semi-transparent overlay at the bottom of the main area showing tutor and student speech in real time. Auto-hides after 4 seconds of inactivity.
* **Control Bar**: Interactive UI for toggling Camera, Microphone (main CTA with ripple rings), Away mode, and End session.
* **Dynamic Status Badges**: Visual indicators in the header ("Connecting...", "Listening...", "Responding...") with color-coded animations.
* **Audio Visualizer**: Animated wave bars on the tutor tile and main area that react when the tutor is speaking.
* **Voice Commands**: 9 supported commands via the browser SpeechRecognition API — camera on/off, front/back camera switch, pause ("give me a moment"), resume ("I'm back"), end session, and checkpoint decisions (save for later, solve now). Command telemetry persisted to Firestore.

## 5. Session & State Management

* **Student Profile Selector**: Modal with 4 pre-configured profiles (Luis/German, Daughter/Math, Daughter/Chemistry, Wife/Technology). Selected profile persisted to localStorage for convenience.
* **Firestore-Backed Learning Tracks**: Each student has tracks with ordered topics. Auto-advances to the next unmastered topic when the current one is completed.
* **Struggle Checkpoint System**: After 2+ struggles on a topic, a checkpoint is created. Tutor asks student whether to solve now or save for later. Decision persisted to Firestore.
* **Language Contract System**: Per-student language policy generating a natural-language contract (immersion/guided bilingual/auto mode) with confusion fallback rules. Injected into ADK session state.
* **Idle Orchestration**: Server-driven (`_idle_orchestrator`) 3-stage progressive check-ins (10s/25s/90s) with auto-away transition. Mic kickoff after 5s of initial silence to start the conversation.
* **Away Mode**: Student can pause the session via voice command ("give me a moment") or button. Tutor goes quiet, mic disables. "I'm back" resumes.
* **Session Hard Limits**: 20-minute timeout (`_session_timer`) with graceful end notification.
* **Privacy Consent Modal**: Mandatory consent screen with expandable "Learn more" section before session start. Consent persisted to localStorage and Firestore.
* **Auto-Reconnect**: WebSocket reconnects with backoff (5 attempts, 3s delay) on connection loss. Reconnect banner shown to user.
* **Latency Instrumentation**: Backend logs `LATENCY response_start_ms` and `LATENCY interruption_stop_ms` for each session.
