# POC 08 — A2A Session Summary: Mini PRD

## Why This Matters

Agent-to-Agent (A2A) interaction is a key differentiator in the 2026 Gemini Live Agent Challenge ("Agentic Interactivity"). It proves to the judges that the project is a mature, hierarchical Multi-Agent System (MAS), not just a single prompt.

From the Judging Strategy:
> "Technical Implementation & Agent Architecture: The sound logic of the agent hierarchy (e.g., using ADK)... proving the agent is executing deterministic, structured orchestration."

The "Parent/Self Updater" (A2A Session Summary) solves a real user problem: live sessions are ephemeral. When the session ends, the student (or parent) needs a tangible artifact (a study guide or progress report) without forcing the low-latency Live audio agent to perform heavy, slow text summarization while the user waits on the call.

---

## The Problem (Without This POC)

| # | Broken behavior | User impact | Root cause in main app |
|---|---|---|---|
| 1 | Amnesic Tutoring | Student finishes a 20-minute session and has no record of what they learned or struggled with. | Live voice sessions leave no lasting artifacts. |
| 2 | End-of-Call Latency | Tutor takes 15 seconds to say goodbye because it's generating a summary. | Forcing the low-latency Gemini Flash audio model to do heavy text synthesis before closing the WebSocket. |
| 3 | Context Window Bloat (1011 Error) | The Live Agent tries to read back 20 minutes of audio history to summarize it, crashing the session. | Lack of model routing; using the wrong tool (real-time voice) for a batch processing job (summarization). |
| 4 | Flat Architecture | The project looks like a basic 2024 chatbot script. | Missing true A2A handoffs. |

**In the demo video, we need to show the live call ending seamlessly, immediately followed by an asynchronous, structured summary appearing on the student's dashboard.**

---

## What "Done" Looks Like

### The A2A Handoff Flow (The "Reflection Agent")

The system must clearly separate the **Live Tutor** (optimized for speed/voice) from the **Reflection Agent** (optimized for deep reasoning/text).

#### Flow (ordered)

1. **Session Completion (Live Tutor)**
   - Student signals end of session ("I'm done for now").
   - Live Tutor acknowledges immediately (<500ms): "Great work today! I'm compiling your study notes now. See you next time!"
   - Live Tutor closes the WebSocket cleanly.

2. **The A2A Trigger (Orchestrator)**
   - The Orchestrator (`main.py`) detects the session close.
   - It gathers the session context (Firestore student profile, topic) and the generated **Session Transcript** (from ADK logs/events).

3. **Background Processing (Reflection Agent)**
   - Orchestrator invokes a background A2A call to the **Reflection Agent** (using Gemini 3.1 Pro or Flash non-live API).
   - The Reflection Agent is prompted strictly to act as an educational analyst. It reviews the transcript to identify:
     - Concepts mastered.
     - Specific struggle points (formulas, vocabulary).
     - Recommended next steps.

4. **Artifact Generation (Database/UI)**
   - Reflection Agent outputs a structured JSON summary.
   - Orchestrator saves this JSON to a new Firestore collection (`session_summaries/{session_id}`).
   - The UI (Dashboard) updates to show the new "Study Guide" artifact.

### Must-Have (POC ships to main app)

| ID | Criterion | How to verify |
|---|---|---|
| **M0** | Instant Call Termination | Live Tutor says goodbye and closes WebSocket without blocking on summary generation. |
| **M1** | A2A Trigger | Orchestrator successfully extracts transcript and triggers background agent upon session end. |
| **M2** | Structured Output | Reflection Agent produces a valid JSON object matching a defined schema (Mastered, Struggles, Next Steps). |
| **M3** | Persistence | The summary JSON is successfully saved to Firestore linked to the specific student and session. |
| **M4** | Accuracy | The summary accurately reflects the actual content of the transcript (no hallucinated topics). |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Model Routing | Prove that the Reflection Agent uses a different/heavier model config (e.g., standard text API) than the Live Tutor. |
| S2 | UI Notification | The frontend shows a non-blocking toast/notification when the summary is ready ("New Study Guide Generated!"). |

### Won't Do (out of scope)

- Sending actual emails or SMS messages to parents (Firestore/UI display is enough for the demo).
- Full long-term Memory/RAG embeddings of the summaries (just storing them per-session is sufficient for the hackathon).

---

## Key Metrics

### Primary (must track in logs)

| Metric | Target | How measured |
|---|---|---|
| **Call Close Latency** | < 1000ms | Time from student saying "goodbye" to WebSocket closure (must NOT block for summary). |
| **Summary Generation Time** | < 10s | Time from A2A trigger to Firestore write completion (background). |
| **JSON Parse Success** | 100% | Reflection Agent output successfully parses against the strict JSON schema schema. |

---

## Architecture Summary

```
┌────────────────────────────────────────────────────────────────┐
│                        BROWSER                                  │
│                                                                 │
│  Student: "I'm done" ──→ WebSocket ──→ Live Tutor               │
│                                                                 │
│  UI Dashboard ←── Watches Firestore (session_summaries/...)     │
└────────────────────────────────────────────────────────────────┘
                                │ Live Audio
                                ▼
┌────────────────────────────────────────────────────────────────┐
│                     FASTAPI (Orchestrator)                      │
│                                                                 │
│  1. Live Tutor closes WS instantly.                             │
│  2. Extract ADK Transcript.                                     │
│  3. A2A Trigger ──→ Initiate Background Task                    │
└────────────────────────────────────────────────────────────────┘
                                │ A2A Call (REST/gRPC, NOT Live WS)
                                ▼
┌────────────────────────────────────────────────────────────────┐
│                REFLECTION AGENT (Gemini 3.1 Pro/Flash Text)     │
│                                                                 │
│  Input: Transcript + Student Profile                            │
│  Output: Structured JSON (Mastered, Struggles, Action Items)    │
│                                                                 │
│  Action: Write to Firestore -> DB triggers UI update.           │
└────────────────────────────────────────────────────────────────┘
```

---

## What Ships to Main App

The POC isolates the A2A handoff mechanism. Once validated, these specific changes go to the main app:

### Backend (`main.py` & `tutor_agent/reflection_agent.py`)

- **Event Listener:** Hook in `main.py` to catch `on_session_end`.
- **New Agent File:** `reflection_agent.py` defining the background text-based Gemini call with `response_mime_type="application/json"`.
- **Firestore DB Update:** Code to push the result to a new `session_summaries` collection.

### NOT shipped (POC-only)

- Mocked transcripts (used for testing the Reflection prompt before plugging into the live ADK stream).

---

## Test Plan (Ordered by Priority)

| # | Scenario | Pass criteria | Tests M# |
|---|---|---|---|
| 1 | **Fast Disconnect** — Student ends session. | WebSocket closes immediately; no lag waiting for DB write. | M0 |
| 2 | **Trigger Success** — End a valid, 3-turn session. | Orchestrator logs show the Reflection Agent was invoked with the correct transcript string. | M1 |
| 3 | **JSON Enforcement** — Pass a mocked transcript to the Reflection Agent. | Agent returns valid JSON containing `mastered_concepts`, `struggle_areas`, and `next_steps` arrays. | M2, M4 |
| 4 | **Firestore Persistence** — Complete the full A2A flow. | Check Firebase Console: a new document exists in `session_summaries/` with correct data. | M3 |
| 5 | **Empty/Null Session** — Connect then disconnect without speaking. | Reflection Agent either safely ignores (no trigger) or returns an "Empty Session" JSON without throwing an error. | M1, M2 |

---

## Timeline

This POC directly supports the **"Impact and Scalability / Architecture"** segment of the Demo Storyboard (Closeout). It should be implemented after the core Live Agent flow (PoC 02) is stable.
