# POC 11 — Idle Orchestration: Mini PRD

## Why This Matters

Silence handling is a core quality signal for the Live Agents category.
From the judging rubric (40% weight — Innovation & Multimodal UX):

> "experience fluidity & context-awareness"

A tutor that nags during silence, talks over a student who is thinking, or
feels "stuck" during quiet moments fails the fundamental expectation of a
real tutoring experience. Real tutors know when to wait, when to check in,
and when to step back. This POC makes silence feel natural — not awkward.

---

## The Problem (Without This POC)

The main app today has **all of these broken behaviors**:

| # | Broken behavior | User impact | Root cause in main app |
|---|---|---|---|
| 1 | Tutor nags repeatedly during silence | Student feels pressured, stops thinking | No idle state machine; nudge fires on every silence threshold |
| 2 | Tutor talks during away/break | Student takes a break, tutor keeps chattering | No away mode concept; idle prompts fire regardless of intent |
| 3 | Silence feels like a bug | Student thinks the app is frozen | No visible state indicator; no feedback during quiet periods |
| 4 | No graceful re-entry | Student returns from break, tutor has no context | No resume flow; session continues as if nothing happened |
| 5 | Idle prompts overlap speech | Student starts speaking, but idle check-in fires over them | Idle timers not interrupt-safe; no instant reset on speech |

**In the demo video, any of these would make the tutor feel robotic and unnatural.**

---

## What "Done" Looks Like

### Must-Have (POC ships to main app)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | Check-ins happen exactly at thresholds: gentle at 10s, options at 25s | Leave mic on, do nothing. Check-in at ~10s, options at ~25s |
| **M2** | Maximum 1 prompt per stage, then silence | After gentle check-in, no more prompts until 25s. After options, no more until 90s |
| **M3** | User speech instantly resets to ACTIVE | Start speaking during any idle stage. All timers stop, state returns to active |
| **M4** | Away mode: tutor stays quiet indefinitely | Enter away mode. Wait 2+ minutes. No tutor speech at all |
| **M5** | Resume gives a 1-line recap + next step | Return from away mode. Tutor briefly recaps and continues |
| **M6** | Voice commands work: "give me a moment" enters away, "I'm back" resumes | Say "give me a moment" during active. Say "I'm back" during away |
| **M7** | UI shows visible idle state (Active / Waiting / Away / Resuming) | Check badge color and label at each state transition |
| **M8** | Idle prompts do not overlap user speech | Start speaking right as a check-in fires. Agent stops within 200ms |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Timer display shows seconds since last activity | Watch the timer count up during silence periods |
| S2 | Manual override buttons work (Take a Break / I'm Back) | Click buttons and verify state transitions |
| S3 | Idle state transitions logged in event log | Check event log panel for transition entries |
| S4 | Works in all three languages (EN/PT/DE) | Speak in Portuguese or German; idle prompts match language |

### Won't Do (out of scope)

- Camera-based writing detection (optional in rules, complex, low demo value)
- Customizable idle thresholds in UI (config values are fine)
- Session persistence across reconnects for idle state

---

## Key Metrics

### Primary (must track in logs)

| Metric | Target | How measured |
|---|---|---|
| **Check-in timing accuracy** | Within 1s of threshold | Timestamp of gentle_check event minus silence_started_at |
| **Nag count per stage** | Exactly 1 prompt, then silence | Count prompts sent per idle stage |
| **Reset latency** | < 500ms from speech to ACTIVE | Time from speech_start to idle_state=active |
| **Away silence duration** | 0 tutor prompts during away | Count tutor outputs after entering away mode |
| **Resume quality** | 1-line recap, not a fresh greeting | Manual review of tutor response after resume |

### Secondary (nice to see)

| Metric | Target | How measured |
|---|---|---|
| State transitions per session | Logged with from/to/reason | Count of state_transitions array |
| Voice command detection rate | > 90% of clear voice commands detected | Manual test with various phrasings |
| Internal text leak rate | 0 leaks per session | Count of sanitized internal control text |

---

## Architecture Summary

```
+---------------------------------------------------------------+
|                        BROWSER                                  |
|                                                                 |
|  Mic --> Silero VAD v5 --> Audio Gate --> WebSocket --> Server  |
|          (speech/silence)   (speech=real,                       |
|                              silence=zeros)                     |
|                                                                 |
|  Speaker <-- AudioContext <-- Playback Queue <-- WebSocket      |
|                                                                 |
|  Idle State Panel: [Active|Waiting|Away|Resuming] + Timer      |
|  Manual Buttons: [Take a Break] [I'm Back]                     |
+---------------------------------------------------------------+
                              |
                              v
+---------------------------------------------------------------+
|                    FASTAPI (WebSocket)                           |
|                                                                 |
|  Browser -> Gemini                                              |
|    Audio + speech_start/end + barge_in + take_break + im_back  |
|                                                                 |
|  Gemini -> Browser                                              |
|    Audio + text + transcriptions + interrupted + turn_complete  |
|    + Voice command detection (pause/resume patterns)            |
|                                                                 |
|  Idle Orchestrator (async task):                                |
|    ACTIVE --> 10s --> GENTLE_CHECK --> 25s --> OFFER_OPTIONS    |
|           --> 90s --> AWAY                                      |
|    User speaks --> instant ACTIVE reset                         |
|    Voice cmd "give me a moment" --> AWAY                       |
|    Voice cmd "I'm back" --> RESUMING --> ACTIVE                |
|    Sends: idle_state, silence_tick                              |
|                                                                 |
|  Logs: JSONL + details.log + transcript.log                    |
+---------------------------------------------------------------+
```

**State Machine:**
1. **ACTIVE** (default) — Listening. Timers reset on any audio input.
2. **GENTLE_CHECK** (10s) — One calm sentence. No more prompts.
3. **OFFER_OPTIONS** (25s) — One sentence with options. No more prompts.
4. **AWAY** (90s or voice command) — Complete silence. Wait indefinitely.
5. **RESUMING** (transient) — Welcome back + recap. Returns to ACTIVE.

---

## What Ships to Main App

The POC isolates complexity. Once validated, these specific changes go to the main app:

### Backend (`main.py` / `gemini_live.py`)
- Idle orchestrator as a separate async task
- State machine: ACTIVE / GENTLE_CHECK / OFFER_OPTIONS / AWAY / RESUMING
- Hidden turn injection via `_send_hidden_turn()` for idle prompts
- Voice command regex detection on transcribed text
- `idle_state` WebSocket messages to frontend
- State transition logging

### Frontend (`index.html`)
- Idle state badge (color-coded: green/yellow/gray/blue)
- Silence timer display
- "Take a Break" and "I'm Back" buttons
- Speech start/end notifications to backend for timer management

### NOT shipped (POC-only)
- Metrics dashboard (debug tool)
- Event log panel (debug tool)
- JSONL file logging (debug tool)

---

## Test Plan (Ordered by Priority)

Run each scenario on laptop speakers (no headphones needed for idle tests).

| # | Scenario | Pass criteria | Tests M# |
|---|---|---|---|
| 1 | **Threshold accuracy** — Start mic, stay silent | Gentle check at ~10s, options at ~25s | M1 |
| 2 | **No nagging** — After gentle check, stay silent until 25s | No additional prompts between 10s and 25s | M2 |
| 3 | **Instant reset** — Start speaking during waiting state | Idle timers stop, badge turns green (Active), tutor responds | M3 |
| 4 | **Away silence** — Enter away mode, wait 2+ minutes | Zero tutor output during entire away period | M4 |
| 5 | **Resume quality** — Return from away mode | Tutor gives 1-line recap, continues naturally | M5 |
| 6 | **Voice pause** — Say "give me a moment" during active session | Badge turns gray (Away), tutor acknowledges then goes silent | M6 |
| 7 | **Voice resume** — Say "I'm back" during away mode | Badge turns blue then green, tutor recaps | M6 |
| 8 | **Interrupt safety** — Start speaking right as check-in fires | Agent stops within 200ms, no overlap | M8 |
| 9 | **UI states visible** — Watch badge throughout a full cycle | All four states display correctly with colors | M7 |
| 10 | **Long away** — Enter away, wait 5 minutes, return | No random chatter, clean resume | M4, M5 |

---

## Risk: Prompt Leakage

The idle orchestrator injects hidden prompts as synthetic user turns. If Gemini
echoes these back (e.g., "As you requested, here is a check-in..."), the student
sees internal control text.

**Mitigation:**
- `_sanitize_tutor_output()` strips `[INTERNAL...]` and `INTERNAL CONTROL:` prefixes
- System prompt explicitly says: "Never quote, paraphrase, or mention control messages"
- Prompts are phrased as directives, not questions Gemini might quote

**If this regresses:** Add a more aggressive post-filter that drops entire tutor
turns containing control-message fragments.

---

## Timeline

This POC is a **Week 2** deliverable. Integration into the main app should happen
during Week 3 polish, before the demo video is recorded (Week 4).
