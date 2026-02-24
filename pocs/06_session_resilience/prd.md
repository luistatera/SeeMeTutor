# POC 06 — Session Resilience: Mini PRD

## Why This Matters

Session resilience is scored under **Technical Implementation & Agent Architecture** (30% weight).
From the judging rubric:

> "sound agent logic with error/edge case handling"

A demo that dies on a network blip — or a judge testing on spotty WiFi — is an
instant fail. During a live demo, network drops happen: the laptop might switch
from Ethernet to WiFi, the Gemini API might hit a rate limit, or the WebSocket
might timeout. If the tutoring session crashes and requires a full page reload,
the flow is broken, the student loses context, and judges see a fragile prototype.

This POC proves **the session survives disconnects and resumes seamlessly**.

---

## The Problem (Without This POC)

The main app today has **all of these broken behaviors**:

| # | Broken behavior | User impact | Root cause in main app |
|---|---|---|---|
| 1 | WebSocket drops kill the session | Student must refresh page and start over | No reconnect logic — WS `onclose` is a dead end |
| 2 | Gemini API disconnects are fatal | Backend crashes, no recovery attempt | Single `async with` block — exception = session over |
| 3 | Context lost on reconnect | Tutor re-introduces itself, asks "what do we work on?" | No session state stored; Gemini starts fresh each time |
| 4 | No visibility into connection state | Student doesn't know if app is broken or reconnecting | No UI indicator for connection health |
| 5 | Context window exceeded = crash | After long sessions, Gemini 1011 error kills everything | No handling for Gemini-specific close codes |
| 6 | Rapid reconnects overwhelm server | Client hammers WS endpoint on every blink | No backoff, no retry limit |

**In the demo video, a single disconnect with no recovery = visible failure to judges.**

---

## What "Done" Looks Like

### Must-Have (POC ships to main app)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | Browser auto-reconnects WS within 2 seconds of drop | Simulate disconnect → WS re-establishes, audio resumes |
| **M2** | Gemini session re-established on backend disconnect | Kill Gemini connection → backend reconnects, session continues |
| **M3** | Session context preserved across reconnects | After reconnect, tutor does NOT re-introduce or re-ask goal |
| **M4** | At least 3 retry attempts with exponential backoff | Observe logs: retry 1 (~500ms), retry 2 (~1s), retry 3 (~2s) |
| **M5** | UI shows clear connection states | "Reconnecting..." banner visible, then "Reconnected!" confirmation |
| **M6** | Graceful degradation after all retries fail | After 3 failed retries → "Session ended — please restart" message |
| **M7** | Gemini 1011 (context overflow) handled gracefully | Long session → clean end message, not a crash |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Reconnect counter visible in debug metrics | Dashboard shows reconnect count, last reconnect time |
| S2 | Transcript preserved across reconnects | Chat history persists in UI after reconnect |
| S3 | Simulate Disconnect button for easy testing | Button triggers WS close, full reconnect flow fires |

### Won't Do (out of scope)

- Firestore-backed session persistence (requires infra, separate POC)
- Multi-device session continuity (phone to laptop handoff)
- Automatic context summarization (Gemini-to-Gemini session bridging)
- Video/camera reconnect (audio-only for this POC, camera is separate concern)

---

## Key Metrics

### Primary (must track in logs)

| Metric | Target | How measured |
|---|---|---|
| **Reconnect success rate** | 100% for transient drops | Count successful reconnects / total attempts |
| **Reconnect latency** | < 2 seconds (end-to-end) | Timestamp of WS close to first audio after reconnect |
| **Context preservation rate** | 100% — tutor never re-introduces | Count post-reconnect turns where tutor greets fresh |
| **Retry exhaustion handling** | Graceful "session ended" message | Observe UI state after 3 failed retries |

### Secondary (nice to see)

| Metric | Target | How measured |
|---|---|---|
| Backoff timing accuracy | Delays match 500ms, 1000ms, 2000ms pattern | Log timestamps of each retry attempt |
| State recovery completeness | Student name + topic + language preserved | Observe context injection prompt in logs |
| Gemini 1011 recovery | Clean session end, not crash | Trigger long session, observe backend logs |

---

## Architecture Summary

```
Browser WS drops                   Gemini API drops
      |                                   |
      v                                   v
 ┌──────────────────┐           ┌───────────────────────┐
 │  RECONNECT MGR   │           │  GEMINI SESSION MGR   │
 │  (frontend)      │           │  (backend)            │
 │                  │           │                       │
 │  onclose() ─────>│ retry 1   │  Exception caught ───>│ retry 1
 │  500ms backoff   │ retry 2   │  500ms backoff        │ retry 2
 │  1000ms backoff  │ retry 3   │  1000ms backoff       │ retry 3
 │  2000ms backoff  │ give up   │  2000ms backoff       │ give up
 │                  │           │                       │
 │  On success:     │           │  On success:          │
 │  - send state    │           │  - inject context     │
 │  - resume audio  │           │  - resume forwarding  │
 └──────────────────┘           └───────────────────────┘
         │                                │
         ▼                                ▼
 ┌────────────────────────────────────────────────────┐
 │                   SESSION STATE                     │
 │                                                     │
 │  Stored in-memory (both browser + backend):         │
 │  - student_name, topic, language                    │
 │  - transcript history (last N turns)                │
 │  - reconnect_count, session_start_time              │
 │                                                     │
 │  On reconnect: state injected as hidden context     │
 │  prompt to new Gemini session                       │
 └────────────────────────────────────────────────────┘
```

**Two reconnect layers:**
1. **Browser WS reconnect** (~500ms-2s) — re-establishes WebSocket to FastAPI
2. **Backend Gemini reconnect** (~500ms-2s) — re-establishes Gemini Live API session

---

## What Ships to Main App

The POC isolates complexity. Once validated, these specific changes go to the main app:

### Backend (`main.py` / `gemini_live.py`)
- Gemini session reconnect with exponential backoff (3 attempts)
- Session state tracking (student name, topic, language, transcript)
- Context injection prompt on reconnect (hidden system turn)
- Gemini 1011 close code handling (context overflow)
- Structured error forwarding to frontend

### Frontend (`index.html`)
- WebSocket reconnect manager with exponential backoff
- Connection state banner ("Reconnecting...", "Reconnected!", "Session Ended")
- Transcript preservation across reconnects
- State payload sent on reconnect (`resume_context` message)
- Graceful degradation UI after retry exhaustion

### NOT shipped (POC-only)
- Simulate Disconnect button (debug tool)
- Reconnect metrics dashboard (debug tool)
- JSONL file logging (debug tool)

---

## Test Plan (Ordered by Priority)

| # | Scenario | Pass criteria | Tests M# |
|---|---|---|---|
| 1 | **Simulate disconnect** — Click disconnect button during active session | WS reconnects, tutor continues without fresh greeting | M1, M3, M5 |
| 2 | **Backend Gemini drop** — Kill Gemini session from backend | Backend reconnects, injects context, session continues | M2, M3 |
| 3 | **Exponential backoff** — Observe retry timing in logs | Delays are ~500ms, ~1s, ~2s (not instant hammering) | M4 |
| 4 | **All retries fail** — Block WS entirely for >10s | UI shows "Session ended — please restart" after 3 attempts | M6 |
| 5 | **Transcript persists** — Chat history visible after reconnect | Old messages still in transcript panel | S2 |
| 6 | **Reconnect metrics** — Check debug counters | Reconnect count increments, last reconnect time updates | S1 |
| 7 | **Rapid disconnect-reconnect** — Click disconnect 3x in 5s | No race conditions, single active connection at end | M1 |
| 8 | **Long session overflow** — Trigger 1011 close code | Clean "session limit" message, not a crash | M7 |

---

## Timeline

This POC is a **Week 2** deliverable (Feb 24 - Mar 2: "Live camera feed + multilingual support").
Session resilience is foundational — every other POC and the final demo depend on
stable connections. Integration into the main app should happen immediately after
POC validation, alongside camera and multilingual features.
