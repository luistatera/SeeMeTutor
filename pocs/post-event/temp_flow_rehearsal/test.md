# POC 99 - Full Hero Flow Rehearsal: Test Checklist

## Setup

```bash
cd pocs/99_full_hero_flow_rehearsal
uvicorn main:app --reload --port 9900
# Open http://localhost:9900
```

**Prerequisites:**
- Laptop with mic + camera (or external webcam)
- Homework or printed worksheet to point camera at
- Speakers or headphones
- Chrome or Edge browser (WebSocket + getUserMedia support)
- Valid GCP auth: `gcloud auth application-default login`
- Vertex AI API enabled in project `seeme-tutor`

---

## Part 1: Full Demo Flow (Run 3x Consecutively)

This is the exact sequence from the demo script. Every run must complete
with 6/6 checklist items green. Run at least 3 times.

### Run Checklist

| # | Step | Expected | Pass? |
|---|---|---|---|
| 1 | Click "Start Session" | Status dot turns green, camera preview shows, mic active | |
| 2 | Point camera at math homework, stay silent 6-8s | Tutor speaks proactively about what it sees. "Proactive Vision" checklist item turns green | |
| 3 | Ask tutor to explain the problem | Tutor explains while writing a whiteboard note. Note card appears with "Live" badge. "Whiteboard note" checklist item turns green | |
| 4 | While tutor is speaking, say "wait, wait" | Audio stops within ~300ms. Tutor acknowledges. "Interruption" checklist item turns green | |
| 5 | Ask a factual question (e.g., "What is the boiling point of water?") | Tutor uses Google Search. Citation card + floating toast appear. "Search citation" checklist item turns green | |
| 6 | Have 3+ back-and-forth exchanges with the tutor | After 3 student responses, "Action moment" checklist item turns green | |
| 7 | Click "Sim Reconnect" | Overlay shows "Reconnecting..." for ~2s. WS reconnects. Tutor continues without fresh greeting. "Reconnect" checklist item turns green | |
| 8 | Verify checklist shows 6/6 green | Progress bar says "6 / 6 completed" in green | |
| 9 | Verify no crashes or error states | No red status dot, no "Error" transcript entries | |

**Run 1:** Pass / Fail  Date: ___________  Notes: _________________________

**Run 2:** Pass / Fail  Date: ___________  Notes: _________________________

**Run 3:** Pass / Fail  Date: ___________  Notes: _________________________

---

## Part 2: Individual Capability Spot-Checks

### 2.1 Proactive Vision

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Camera on, student silent for 6s | Tutor speaks up with observation about visible work | |
| 2 | Camera on, student silent for 9s (no response to poke) | Harder nudge triggers, tutor speaks | |
| 3 | Camera off, student silent for 10s | Tutor does NOT speak (no false proactive trigger) | |
| 4 | Tutor speaks proactively, references camera content | Speech mentions what is visible ("I can see...") | |
| 5 | Proactive trigger count increments in metrics | mProactive shows correct count | |

### 2.2 Whiteboard Notes

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Tutor writes a note during explanation | Note card appears in Whiteboard panel | |
| 2 | Note has title, content, and type badge | All fields populated, no empty cards | |
| 3 | Note appears while tutor audio is playing | "Live" sync badge shown | |
| 4 | Duplicate note is blocked | If tutor repeats same content, deduped (no duplicate card) | |
| 5 | WB Sync rate > 50% | Metric shows majority of notes synced with speech | |
| 6 | Multiple notes in one session | At least 2 notes appear, all properly rendered | |

### 2.3 Interruption Handling

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Say "wait" during tutor speech | Audio stops, tutor acknowledges ("Sure!" etc.) | |
| 2 | Say "hold on" during tutor speech | Same behavior as "wait" | |
| 3 | Interrupt and ask a different question | Tutor follows new topic, does not return to old one | |
| 4 | Let tutor finish naturally (no interrupt) | Audio plays fully, turn_complete fires, status returns to listening | |
| 5 | Gemini interruption count increments | mInterrupts shows correct count after each interrupt | |
| 6 | No self-interruption from echo | Tutor completes 5 turns on laptop speakers without interrupting itself | |

### 2.4 Search Grounding

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Ask "What is the atomic number of carbon?" | Tutor responds with "6", citation card appears | |
| 2 | Ask "What is the formula for the area of a circle?" | Tutor responds with pi*r^2, citation appears | |
| 3 | Citation toast appears and fades after ~7s | Floating toast in bottom-right, auto-dismisses | |
| 4 | Citation card shows source domain and snippet | cit-source and cit-snippet populated | |
| 5 | Multiple citations in one session | Ask 2+ factual questions, all citations appear | |
| 6 | Non-factual question does NOT trigger search | Ask "How do I approach this problem?" - no citation | |

### 2.5 Action Moment

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | 3+ student-tutor exchanges | Action moment checklist item turns green | |
| 2 | Metric tracks correctly | After detection, checklist updates immediately | |
| 3 | Exchange count resets after reconnect | New session starts fresh (expected - new Gemini session) | |

### 2.6 Reconnect Simulation

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Click "Sim Reconnect" | Overlay appears with spinner for ~2s | |
| 2 | WS reconnects after 2s | Overlay disappears, status returns to green | |
| 3 | Transcript shows "Session context restored" | resume_applied event visible | |
| 4 | Tutor does NOT start with fresh greeting | No "Welcome!" or "What are we working on today?" | |
| 5 | Audio/mic still work after reconnect | Can speak and hear tutor after reconnect | |
| 6 | Camera still sends frames after reconnect | Video frames counter increments | |

---

## Part 3: Integration Conflict Tests

These tests specifically target interference between capabilities.

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Whiteboard + Interrupt: Note queued, then student interrupts | Interruption fires, note still delivers (or is dropped gracefully) | |
| 2 | Search + Proactive: Factual question right after proactive trigger | Both search citation and proactive metrics correct | |
| 3 | Whiteboard + Search in same turn: Tutor explains fact with both | Both note card and citation card appear | |
| 4 | Interrupt + Reconnect: Interrupt, then immediately reconnect | Both checklist items turn green, session recovers | |
| 5 | Rapid interrupts (3x in 30s): Interrupt tutor 3 times quickly | All interrupts tracked, no stuck state, tutor responds each time | |
| 6 | Long session (5+ min): Leave running with camera for 5 min | No memory leak, no WS disconnect, proactive triggers continue | |

---

## Part 4: Metrics Accuracy

| # | Metric | Verify |
|---|---|---|
| 1 | Proactive count | Matches number of times tutor spoke without being asked |
| 2 | WB Notes count | Matches number of note cards rendered in whiteboard panel |
| 3 | Interrupt count | Matches number of times student interrupted |
| 4 | Citations count | Matches number of citation cards rendered |
| 5 | Turns count | Matches number of TURN_COMPLETE events in event log |
| 6 | Video frames | Non-zero when camera is active, stops when camera off |
| 7 | VAD Barge-ins | Matches number of barge_in events sent from client |
| 8 | Pokes/Nudges | Matches idle orchestrator events in event log |

---

## Part 5: Error Handling

| # | Test | Expected | Pass? |
|---|---|---|---|
| 1 | Start without camera permission | Camera shows "Camera off", audio still works | |
| 2 | Start without mic permission | Error message, session does not start | |
| 3 | Server not running, click Start | Connection error shown, clean state | |
| 4 | Close and reopen tab during session | Previous session ends, new tab can start fresh | |
| 5 | Click Stop then Start quickly | Clean restart, no ghost state from previous session | |

---

## Sign-off

| Criteria | Status |
|---|---|
| 3 consecutive full demo runs pass | |
| All Part 2 spot-checks pass | |
| All Part 3 integration tests pass | |
| Metrics accuracy verified | |
| Error handling verified | |
| **Ready for demo video recording** | |

**Tester:** _______________  **Date:** _______________
