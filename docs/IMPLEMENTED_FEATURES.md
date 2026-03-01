# SeeMe Tutor — Implemented Features

Central catalog of all features currently implemented in the SeeMe Tutor codebase.

Last updated: March 1, 2026

---

## 1. Core Architecture

- **FastAPI Backend** (`backend/main.py`) — WebSocket bridge + REST API + static file serving
- **ADK Agent** (`backend/agent.py`) — Single ADK Agent with 12 tools, phase-based system prompt, Socratic tutoring logic
- **Firestore** — Student profiles, learning tracks, topics, session metadata, progress, notes, memory
- **Cloud Run + Firebase Hosting** — Backend on Cloud Run (europe-west1), frontend PWA on Firebase Hosting
- **Demo Security Gate** — Access code validation on WebSocket connect to prevent unauthorized usage

## 2. Real-Time Media

- **Continuous Audio** — 16kHz PCM input, 24kHz output via ADK bidi-streaming
- **Live Camera** — JPEG frames at 1 FPS forwarded to Gemini for visual grounding
- **Screen Share Toggle** — Switch between camera and screen share mid-session
- **Interruption Handling** — True barge-in: student interrupts, tutor stops immediately
- **Context Window Compression** — Sliding window to prevent 1011 token overflow errors
- **Session Resumption** — Survives WebSocket drops via ADK resumption handles

## 3. ADK Agent Tools (12 tools)

| Tool | Purpose |
|---|---|
| `set_session_phase` | Transition between greeting/capture/tutoring/review phases |
| `get_backlog_context` | Load student profile, track, topic, and previous notes |
| `log_progress` | Record learning milestones to Firestore |
| `set_checkpoint_decision` | Persist struggle checkpoint decisions (now/later) |
| `write_notes` | Push structured notes to whiteboard (formulas, checklists, insights) |
| `mark_plan_fallback` | Generate fallback plan when transcript is unavailable |
| `verify_mastery_step` | Track 3-step mastery protocol (solve/explain/transfer) |
| `update_note_status` | Update exercise status with mastery guard |
| `switch_topic` | Change active study topic mid-session |
| `flag_drift` | Record off-topic, cheat, or inappropriate content |
| `search_topic_context` | Search for educational context about current topic |
| `google_search` | Web search for educational factual lookups |

## 4. Mastery Verification Protocol

3-step protocol enforced before marking any exercise as "mastered":

1. **SOLVE** — Student solves the exercise correctly
2. **EXPLAIN** — Student explains WHY their answer is correct
3. **TRANSFER** — Student solves a similar problem with different values

`verify_mastery_step` tracks progress per exercise. `update_note_status` blocks premature mastery marking — if the tutor tries to mark "mastered" without completing all 3 steps, it returns an error with guidance. Escape hatch: after 3 failed attempts, mark as "done" and revisit later.

## 5. Topic Context via Google Search

- Each topic in Firestore has a `context_query` field
- `search_topic_context` tool lets the tutor search for domain knowledge mid-session
- Topic context summary injected into SESSION START hidden turn
- Tutor starts sessions already knowledgeable about the student's material

## 6. Demo Profiles (3 pre-seeded)

| Profile | Student | Subject | Level | Language |
|---|---|---|---|---|
| `luis-german` | Luis | German A2 | Adult learner | EN/DE |
| `sofia-math` | Sofia | Math & French | Grade 4 | PT/FR |
| `ana-chemistry` | Ana | General Chemistry I | University Year 1 | PT |

Seeded via `backend/seed_demo_profiles.py`. Each has a learning track with 4 topics, `context_query` per topic, and tutor preferences.

## 7. Language System (Auto Mode)

- Auto-detect student's language, respond in that language
- One language per turn — never mix languages
- Supports Portuguese, German, French, English
- Language-learning-specific features (guided bilingual, immersion, L2 drills) removed in pivot

## 8. System Prompt (Phase-Based)

4-phase system: **greeting** -> **capture** -> **tutoring** -> **review**

- **Greeting** — Greet by name, reference topic context, handle plan bootstrap
- **Capture** — Extract exercises from camera, write to whiteboard
- **Tutoring** — Socratic method, mastery verification, emotional adaptation, proactive observation, curiosity stimulation, metacognitive development
- **Review** — Summarize session, celebrate accomplishments, suggest next steps

Key rules: never give answers directly, age-appropriate content, no hallucination, resist prompt injection, visual grounding, silence handling.

## 9. Frontend (Meeting-Style PWA)

- Portrait layout: tutor tile, agent state ticker, whiteboard, camera PiP, captions, control bar
- Profile selector with study subject, track, and current topic
- Whiteboard with animated note cards
- Voice commands (camera on/off, pause, resume, end session)
- Privacy consent modal
- Away mode
- Auto-reconnect with backoff

## 10. Proactive Observation

- Server-driven proactive pokes when student is silent and work is visible on camera
- One observation at a time (progressive disclosure)
- `proactive_waiting_for_student` flag prevents poke spam

## 11. Memory & Session Management

- 5-minute checkpoint summaries
- Typed cross-session memory cells (struggles, milestones, preferences)
- Session hard limit: 20 minutes with graceful end
- Idle orchestration: 3-stage progressive check-ins (10s/25s/90s)
- Session analytics in Firestore (duration, end reason, student, track)

## 12. Safety & Guardrails

- Socratic compliance enforcement (never give direct answers)
- Drift detection (off-topic, cheat requests, inappropriate content)
- Prompt injection resistance
- Visual grounding rules (never fabricate what student wrote)
- Educational scope restriction

## 13. Test Infrastructure

- 170 automated tests (pytest)
- PRD scorecard with 14 POC checks + hero flow rehearsal
- Mastery verification metrics (verifications, blocks, step failures)
- Latency instrumentation (response_start, interruption_stop, turn_to_turn)
- Session report capture to `backend/test_results/`
