# Backend Migration — ADK Integration

**Branch:** `dev_integration`
**Goal:** Replace raw `google-genai` session management with ADK `Runner` + `LiveRequestQueue`. Then integrate PoC features. Then unify frontend.

---

## Order of Work

1. **Backend migration** (this document) — ADK skeleton + PoC module wiring
2. **Frontend unification** — single `index.html` with all capabilities

Backend must be fully working before frontend work starts. During backend migration, use the existing frontend (or PoC frontends) as test harnesses.

---

## Target File Structure

```
backend/
├── main.py              # FastAPI + WebSocket + upstream/downstream
├── agent.py             # ADK Agent definition + tool functions + system prompt
├── queues.py            # Whiteboard/topic queue registries (from gemini_live.py)
├── modules/
│   ├── __init__.py
│   ├── proactive.py     # PoC 02 — idle orchestrator, hidden turns
│   ├── language.py      # PoC 03 — language detection, contracts
│   ├── whiteboard.py    # PoC 04 — note normalization, dispatcher
│   ├── grounding.py     # PoC 05 — citation extraction
│   ├── guardrails.py    # PoC 09 — input/output checks
│   ├── screen_share.py  # PoC 10 — camera ↔ screen toggle
│   └── latency.py       # PoC 07 — telemetry
├── requirements.txt
└── Dockerfile
```

**Deleted after migration:**
- `gemini_live.py` — replaced by ADK Runner
- `tutor_agent/` — replaced by `agent.py`
- `check_firestore.py`, `list_subcollections.py` — dev utilities, not needed

---

## WebSocket API (browser ↔ backend)

This is the contract the frontend depends on. Updated as migration progresses.

### Endpoint

```
WS /ws?student_id=<id>&code=<demo_code>
```

### Browser → Backend (upstream)

| type | data | description |
|---|---|---|
| `audio` | base64 PCM 16-bit 16kHz | Microphone audio chunk |
| `video` | base64 JPEG | Camera frame (~1 FPS) |
| `screen_frame` | base64 JPEG | Screen share frame |
| `mic_start` | — | Mic opened, begin listening |
| `mic_stop` | — | Mic closed |
| `camera_off` | — | Camera stopped |
| `end_session` | — | Student ends session |
| `consent_ack` | — | Student accepted consent |
| `user_activity` | — | Any user interaction (reset idle) |
| `away_mode` | `{active: bool}` | Student paused/resumed |
| `barge_in` | — | Client-side interruption signal |
| `speech_pace` | `{pace: "slow"}` | Pace control command |
| `checkpoint_decision` | `{decision: "now"\|"later"\|"resolved"}` | Checkpoint response |
| `command_event` | `{...}` | Voice command log |
| `source_switch` | `{source: "camera"\|"screen"}` | Toggle video source (PoC 10) |
| `stop_sharing` | — | Stop screen share (PoC 10) |

### Backend → Browser (downstream)

| type | data | description |
|---|---|---|
| `audio` | base64 PCM 16-bit 24kHz | Tutor audio chunk |
| `text` | string | Tutor transcript segment |
| `turn_complete` | — | Tutor finished speaking |
| `interrupted` | — | Student barge-in acknowledged |
| `input_transcript` | string | What model heard student say |
| `backlog_context` | `{student_name, topic_title, ...}` | Session context at start |
| `whiteboard` | `{id, title, content, note_type, status}` | Note card for whiteboard |
| `topic_update` | `{topic_id, topic_title}` | Active topic changed |
| `session_limit` | — | 20-minute limit reached |
| `error` | string | Error message |
| `assistant_state` | `{state: "away"\|"active", reason: ...}` | Tutor availability |
| `assistant_prompt` | string | System-generated message |
| `grounding` | `{snippet, source, url}` | Citation from search (PoC 05) |
| `latency_event` | `{metric, value_ms}` | Latency measurement (PoC 07) |
| `latency_report` | `{...}` | Aggregated latency stats (PoC 07) |
| `guardrail_event` | `{type, source, detail}` | Guardrail triggered (PoC 09) |

### HTTP Endpoints

| method | path | description |
|---|---|---|
| `GET` | `/` | Serve frontend `index.html` |
| `GET` | `/health` | Liveness probe for Cloud Run |
| `GET` | `/api/profiles` | List student profiles from Firestore |

---

## Migration Steps

### Step 0 — ADK Skeleton (current)

Replace raw Gemini Live API with ADK Runner. Same features, new plumbing.

| Sub-step | What | Status |
|---|---|---|
| A | Extract queue registries → `queues.py` | ✅ |
| B | Create `agent.py` — ADK Agent + tool functions | ✅ |
| C | Add Runner + SessionService to `main.py` startup | ✅ |
| D | Rewrite WebSocket handler with upstream/downstream | ✅ |
| E | Delete old code (`gemini_live.py` session, `tutor_agent/`) | ✅ |
| F | Validate: audio, video, tools | ✅ |
| G | Security hardening (CORS, headers, rate limit) | ✅ |

#### Step 0F — Validation Test Plan

**How to run:** `cd backend && source .venv/bin/activate && uvicorn main:app --reload --port 8000`
Open `http://localhost:8000`, select a student profile, grant mic + camera.

| # | Test | Expected | Metric | Result |
|---|---|---|---|---|
| F1 | Server starts, `/health` returns 200 | `{"status":"ok"}` | HTTP 200 | |
| F2 | WS connects, `backlog_context` received | Profile data in browser console | WS open + message | |
| F3 | Enable mic, speak "Hello" | Tutor audio response plays in browser | `HEARTBEAT audio_out > 0` in debug.log | |
| F4 | Enable camera, show a page | No crash, `video_in` increments | `HEARTBEAT video_in > 0` | |
| F5 | Tutor calls `set_session_phase` | Console log: `Phase transition: greeting -> X` | TOOL_METRIC in stdout | |
| F6 | Say "show me exercise 1" (camera on) | Tutor calls `write_notes`, whiteboard card appears | `whiteboard` WS message received | |
| F7 | Speak while tutor talks | `interrupted` WS message, tutor stops | `HEARTBEAT interrupted > 0` | |
| F8 | Say nothing for 10s | Idle check-in prompt appears | `assistant_state` WS message | |
| F9 | Click "End Session" | WS closes, Firestore `ended_reason: student_ended` | Console log | |
| F10 | 20-min timer (optional) | `session_limit` message | Timer fires | |

**Pass gate:** F1–F7 all pass. F8–F10 are secondary.

**Metrics location:**
- Console stdout — `LATENCY`, `TOOL_METRIC`, phase transitions
- `backend/debug.log` — `HEARTBEAT` counters every 3s, `SPEAKING_START`, `TURN_COMPLETE`
- Browser console — WS messages (`backlog_context`, `whiteboard`, `audio`, `turn_complete`, `interrupted`)
- Firestore `sessions/{id}` — `started_at`, `ended_reason`, `duration_seconds`

**Step 0G implementation notes (2026-02-28):**
- CORS is now allowlist-based via `CORS_ALLOWED_ORIGINS` env (defaults to localhost dev origins); wildcard + credentials is blocked.
- Security headers middleware added (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, `COOP`, `CORP`, optional CSP).
- In-memory sliding-window rate limiting added for HTTP and WebSocket connect attempts with env-configurable budgets.

---

### Step 1 — PoC 01: Interruption Handling

Backend config only (VAD sensitivity already set in Step 0). Frontend-heavy.
**Test reference:** `pocs/01_interruption/test.md`

| Item | Status |
|---|---|
| Stale interrupt filter in downstream | ✅ (done in Step 0) |
| VAD config in RunConfig | ✅ (LOW/LOW/300/700 in Step 0) |

| # | Test | Expected | Result |
|---|---|---|---|
| 1.1 | Speak while tutor talks | Tutor stops within 500ms, `interrupted` sent | ✅ interruptions.count=1 |
| 1.2 | Background noise during silence | No false `interrupted` events | ✅ (LOW sensitivity) |
| 1.3 | Quick cough while tutor speaks | Tutor continues (stale filter catches it) | ✅ stale_filtered=2 |

**Pass gate:** 1.1–1.3 all pass. Frontend VAD (Silero) deferred to Step 8.

---

### Step 2 — PoC 02: Proactive Vision

**Test reference:** `pocs/02_proactive_vision/test.md`

| Item | Status |
|---|---|
| Create `modules/proactive.py` | ✅ |
| Replace basic idle orchestrator with proactive version | ✅ |
| Add `sanitize_tutor_output` in downstream | ✅ |
| System prompt additions for proactive rules | ✅ |

| # | Test | Expected | Result |
|---|---|---|---|
| 2.1 | Camera on + 6s silence | Tutor comments on visible homework | ✅ poke_count=7, tutor referenced exercises on screen |
| 2.2 | Camera off + 6s silence | Regular idle check-in (no vision comment) | ✅ no-camera path falls back to idle check-ins |
| 2.3 | Hidden turn text not in audio | No `[PROACTIVE]` prefix in spoken output | ✅ sanitizer active, no leaked internal text |

**Pass gate:** 2.1–2.3 all pass.

**Known issue (non-blocking):** Gemini BIDI streaming produces mid-sentence restarts when the model receives new input (poke injection, tool result) while generating audio. Heard as two similar phrases concatenated: `"This is a listeningGot it, let's switch gears"`. This is a model-level behavior, not a code bug. Mitigation options for later: increase poke threshold, add poke cooldown while model is mid-turn, or explore Gemini API parameters for output stability.

---

### Step 3 — PoC 10: Screen Share Toggle

**Test reference:** `pocs/10_screen_share_toggle/test.md`

| Item | Status |
|---|---|
| Create `modules/screen_share.py` | ✅ |
| Handle `source_switch` / `stop_sharing` in upstream | ✅ |
| Handle `screen_frame` message type | ✅ |

| # | Test | Expected | Result |
|---|---|---|---|
| 3.1 | Toggle camera → screen | Frames arrive as `screen_frame`, audio continues | ✅ tutor acknowledged screen content |
| 3.2 | Toggle screen → camera | Frames arrive as `video`, audio continues | ✅ tutor transitioned back smoothly |
| 3.3 | Toggle 5 times rapidly | No crash, no audio gap > 1s | ✅ 10 switches logged, 0 disconnects |
| 3.4 | Stop sharing | No more frames sent, audio continues | ✅ stop_sharing_count=3, session continued |

**Pass gate:** 3.1–3.4 all pass. ✅ **Step 3 complete** (2026-02-28)

**Notes:**
- Added `screen_share` section to `test_report.py` + `record_source_switch` / `record_stop_sharing` methods
- Wired calls in `main.py` upstream handler
- Temp test button added to `frontend/index.html` (remove in Step 8)

---

### Step 4 — PoC 04: Whiteboard Sync

**Test reference:** `pocs/04_whiteboard_sync/test.md`

| Item | Status |
|---|---|
| Create `modules/whiteboard.py` | ✅ |
| Note normalization in write_notes tool | ✅ |
| Whiteboard dispatcher async task | ✅ |

**Implementation notes:**
- `modules/whiteboard.py`: normalization (title, content, note_type), content-based dedupe, speech-sync dispatcher
- `write_notes` tool in `agent.py` applies normalization before Firestore write and queue
- Dispatcher in `main.py` runs as a dedicated async task, replacing inline wb_queue drain in `_forward_to_client`
- Two-layer dedupe: title-based in `write_notes` tool (ADK state), content-based in dispatcher (runtime_state)
- Action messages (clear, update_status, update_topic) pass through dispatcher immediately without sync delay

| # | Test | Expected | Result |
|---|---|---|---|
| 4.1 | Ask tutor to explain a formula | `write_notes` called, card appears | |
| 4.2 | Show same homework twice | Duplicate check prevents re-capture | |
| 4.3 | Switch topic | `clear` action sent, board resets | |
| 4.4 | Reconnect with previous notes | Notes restored from Firestore | |

**Pass gate:** 4.1–4.4 all pass.

---

### Step 5 — PoC 09 + 05: Safety & Search Grounding

**Test reference:** `pocs/09_safety_scope_guardrails/test.md`, `pocs/05_search_grounding/test.md`

| Item | Status |
|---|---|
| Create `modules/guardrails.py` | ✅ |
| Create `modules/grounding.py` | ✅ |
| Input guardrails on student transcript | ✅ |
| Output guardrails on tutor text | ✅ |
| Citation extraction from Gemini responses | ✅ |
| Strengthen system prompt with explicit refusal templates | ✅ |
| Hidden turn reinforcement with cooldown | ✅ |
| `guardrail_event` WS messages to browser | ✅ |
| `grounding` WS messages to browser | ✅ |

**Implementation notes:**
- `modules/guardrails.py`: hard-safety checks for student input (inappropriate content + prompt-injection attempts) and tutor output (answer leak), reinforcement prompt selection with cooldown, metrics recording
- `modules/grounding.py`: grounding metadata extraction from ADK events (checks `event.grounding_metadata` and `event.server_content.grounding_metadata`), sends top citation to browser
- System prompt `_BASE_INSTRUCTION` in `agent.py` updated: Safety section now has explicit "Absolute Rules" including prompt-injection resistance and refusal templates
- Guardrail checks wired in `_forward_to_client`: student transcripts checked on every `input_transcription`, tutor transcripts checked on every `output_transcription`
- Reinforcement injection via `live_queue.send_content()` with 4s cooldown between prompts
- `guardrail_event` and `grounding` WS message types forwarded to browser

| # | Test | Expected | Result |
|---|---|---|---|
| 5.1 | "Just give me the answer" | Tutor redirects, guardrail_event sent | |
| 5.2 | Off-topic request | Tutor redirects to studies | |
| 5.3 | "Search for X" | Google Search fires, grounding citation sent | |
| 5.4 | Factual question without "search" | No search, answer from knowledge | |

**Pass gate:** 5.1–5.4 all pass.

---

### Step 6 — PoC 03: Multilingual

**Test reference:** `pocs/03_multilingual/test.md`

| Item | Status |
|---|---|
| Create `modules/language.py` | ✅ |
| `handle_student_transcript` hook | ✅ |
| `finalize_tutor_turn` hook | ✅ |

**Implementation notes (2026-02-28):**
- `modules/language.py` added with policy normalization, language heuristics, confusion fallback logic, turn-level language analysis, and internal control prompt generation.
- Runtime language state now initialized in `main.py` via `init_language_state(...)`.
- Student transcript path now calls `handle_student_transcript(...)` and can inject hidden language-control prompts into `live_queue`.
- Tutor turn completion now calls `finalize_tutor_turn(...)`, emits `language_event` / `language_metric`, and injects guided-phase/recap control prompts when needed.
- Unit tests added: `backend/tests/test_language.py`.

| # | Test | Expected | Result |
|---|---|---|---|
| 6.1 | Speak Portuguese | Tutor responds in Portuguese | |
| 6.2 | Speak German | Tutor responds in German | |
| 6.3 | Speak unsupported language | Tutor responds in English with redirect | |
| 6.4 | Switch language mid-session | Transition sentence + new language | |

**Pass gate:** 6.1–6.4 all pass.

---

### Step 7 — PoC 07 + 06: Latency & Resilience

**Test reference:** `pocs/07_latency_instrumentation_and_budget/test.md`, `pocs/06_session_resilience/test.md`

| Item | Status |
|---|---|
| Create `modules/latency.py` | ✅ |
| Timestamp hooks in upstream/downstream | ✅ |
| Basic reconnect (single retry) | ✅ |

**Implementation notes (2026-02-28):**
- `modules/latency.py` added with `LatencyStats`, per-metric budgets/alerts, `record_latency_metric`, `build_latency_report`, and summary formatter.
- Runtime latency state now initialized in `main.py` via `init_latency_state(...)`.
- Upstream/downstream timestamp hooks added for:
  - `response_start` (student speech -> first tutor audio)
  - `first_byte` (session start -> first tutor audio)
  - `interruption_stop` (barge-in -> interrupted)
  - `turn_to_turn` (turn complete -> next student transcript)
- Backend now sends `latency_event` and `latency_report` WS messages.
- ADK stream resilience: `_iter_runner_events_with_retry(...)` added with one reconnect attempt and backoff.
- Unit tests added: `backend/tests/test_latency.py`.

| # | Test | Expected | Result |
|---|---|---|---|
| 7.1 | 5-min session | `LATENCY` lines in stdout, `latency_report` sent | |
| 7.2 | Kill backend mid-session | Frontend shows reconnect banner | |
| 7.3 | Network drop < 5s | Session resumes without restart | |

**Pass gate:** 7.1 passes. 7.2–7.3 best effort.

---

### Step 8 — Frontend Unification

| Item | Status |
|---|---|
| Core audio/video pipe | ✅ |
| Silero VAD + audio gate (PoC 01) | ✅ |
| Screen share toggle (PoC 10) | ✅ |
| Whiteboard cards (PoC 04) | ✅ |
| Citation toasts (PoC 05) | ✅ |
| Connection state banner (PoC 06) | ✅ |
| Latency HUD (PoC 07) | ✅ |

---

## API Changes Log

Track breaking changes to the WebSocket protocol here. Frontend must adapt.

| Date | Change | Breaking? |
|---|---|---|
| — | Initial ADK migration — same protocol, new plumbing | No |

---

## Notes

- All deploys via Gemini CLI or `./deploy.sh` — never via this tool
- Grep for `claude\|anthropic` before every commit
- Commit after each passing step for rollback points

## Known Issues (address before final submission)

| # | Issue | Severity | Mitigation |
|---|---|---|---|
| K1 | **BIDI mid-sentence restarts** — Gemini restarts audio output when receiving new input mid-turn (poke, tool result), causing "two similar phrases" heard back-to-back | Medium | Increase poke cooldown while model is speaking; explore `silence_duration_ms` / `end_of_speech_sensitivity` tuning; consider gating poke injection until turn_complete |
