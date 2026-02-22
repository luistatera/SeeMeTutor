# SeeMe Tutor — Implemented Features

This document provides a central, up-to-date catalog of all features currently implemented and live in the SeeMe Tutor codebase.

---

## 1. Core Architecture & Infrastructure

* **FastAPI Backend Engine**: A high-performance Python backend serving both static assets and managing live API connections.
* **WebSocket Bridge**: Full-duplex WebSocket connection (`/ws/{student_id}`) bridging the browser's WebRTC media streams and the backend logic.
* **Gemini ADK Integration**: Wraps the Google Agent Development Kit (ADK) `Runner` into a `ADKLiveSession`, managing bidirectional streaming for audio, video, and text events with the Gemini Live API.
* **Cloud Run & Firebase Ready**: Backend health checks (`/health`) and structured deployment config suited for GCP Cloud Run. Frontend set up as a PWA for Firebase Hosting.
* **Firestore Session Analytics (Optional)**: Integration with Google Cloud Firestore to log session metrics, track progress against user-specific learning paths, and load existing user profiles (`_load_or_seed_backlog_context`).
* **Demo Security Gate**: WebSocket connections validate a `DEMO_ACCESS_CODE` environmental variable to prevent unauthorized usage and control costs.

## 2. Real-Time Media & AI Behavior

* **Continuous Audio Streaming**: The client captures 16kHz PCM audio via WebRTC and streams it as base64 to the backend, which forwards it to Gemini.
* **Vision/Camera Grounding**: The client captures frames (JPEG) from the user's camera periodically and streams them. The Gemini model uses this to \"see\" the homework and provide proactive visual assistance.
* **Barge-in / Interruption Handling**: True interruption functionality where the student can cut off the tutor mid-sentence. The system detects ADK `interrupted` events and immediately halts ongoing playback.
* **Socratic & Multilingual Tutoring**: Enabled via dynamic profile injection. The AI acts as a guide (not an answer engine) and switches languages fluidly (e.g., German practice (`luis-german`) or chemistry in Portuguese (`daughter-chem-university`)).

## 3. Frontend User Experience (PWA)

* **Single-Page Application (SPA)**: Lightweight, mobile-first responsive layout tailored for iPad/tablet and desktop usage without requiring installations.
* **Session Control Bar**: Interactive UI for toggling the Microphone, toggling the Camera, forcing an "Away" state, or cleanly ending the session (`ctrl-btn`).
* **Dynamic Status Badges**: Visual indicators ("Waiting for connection...", "Listening...", "Tutor is speaking...") backed by CSS animations mirroring the AI's internal state.
* **Audio Visualizer**: Animated wave bars (`audio-visualization`) that react to indicate active audio streams and make the AI feel "alive".
* **Transcript Panel**: A real-time, scrolling closed-captioning box that displays the AI's spoken responses as text for better accessibility and comprehension.
* **Voice Command Mode Support**: CSS rules enabling a layout optimized entirely for voice-driven interaction.

## 4. Session & State Management

* **Idle Orchestration**: The backend securely monitors for user silence (`_idle_orchestrator`). If the user goes idle, the system automatically transitions them to an \"Auto-Away\" state to save API tokens and resets when they return.
* **Session Hard Limits**: A built-in timeout mechanism (`_session_timer` mapped to 20 minutes) ensures no session runs indefinitely, preventing run-away token costs if the user forgets to close the tab.
* **Profile Auto-Seeding**: The system automatically provisions mock database profiles (`DEFAULT_PROFILE_SEEDS`) with pre-defined learning tracks (e.g. \"Linear Equations\", \"German A2\") if a new student connects without existing database records.
