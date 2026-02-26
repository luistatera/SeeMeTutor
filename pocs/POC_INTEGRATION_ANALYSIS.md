# PoC Integration Analysis — What's Transferable and What Needs Refactoring

## The Core Problem

Every PoC is a **standalone monolith**: each has its own `main.py` (400-1300 lines) with a full FastAPI app, its own `index.html` (890-2100 lines), its own Gemini session setup, its own WebSocket handler, and its own copy of shared boilerplate. The unique, valuable logic for each capability is **buried inside the boilerplate**, not isolated in importable functions or modules.

This means you can't just `from poc_01 import interruption_handler` — you have to **surgically extract** the core functions from each PoC's monolith and weave them into the main app's already-existing WebSocket handler and frontend.

---

## Per-PoC Assessment

### PoC 01 — Interruption Handling (540 lines backend, 907 lines frontend)

**Core value:** Silero VAD client-side, audio gate, barge-in confirmation (220ms + loudness), drain-aware turn_complete, LOW sensitivity config.

**Isolation status: ❌ NOT ISOLATED**
- The core interruption logic lives ENTIRELY in the frontend (`index.html`) — Silero VAD init, audio gate, barge-in confirmation, noise floor calibration
- Backend changes are minimal: just config values (`START_SENSITIVITY_LOW`, `END_SENSITIVITY_LOW`, `silence_duration_ms: 700`) and ignoring stale `interrupted` events
- There are NO unique backend functions — the PoC-specific logic is all inline in `_forward_browser_to_gemini` and `_forward_gemini_to_browser`

**What to extract:**
- **Frontend:** Silero VAD setup, audio gate logic, barge-in confirmation pipeline, drain-aware playback — these are all inline in the HTML
- **Backend:** Config values + the `if not assistant_speaking: ignore interrupted` logic within the Gemini forwarder

**Refactoring needed:**
1. Frontend: Extract VAD + audio gate + barge-in into a clearly marked `// === INTERRUPTION MODULE ===` section in the JS with documented entry points
2. Backend: The changes are config-level. No refactoring needed — just port the config values and the stale-interrupt guard

**Hook points needed:**
- `on_interrupted` — ignore stale interrupted events when `assistant_speaking == False`
- `gemini_config` — VAD sensitivity values (`START_SENSITIVITY_LOW`, `END_SENSITIVITY_LOW`, `silence_duration_ms: 700`)
- Frontend: audio playback pipeline (drain-aware turn_complete, barge-in confirmation gate)

**Transfer difficulty: MEDIUM** (frontend extraction is the work; backend is trivial)

---

### PoC 02 — Proactive Vision (1211 lines backend, 1308 lines frontend)

**Core value:** Idle orchestrator with proactive triggers, hidden turn injection, session goal flow, progressive disclosure enforcement.

**Isolation status: ⚠️ PARTIALLY ISOLATED**
- Core functions ARE named and extractable:
  - `_check_proactive_trigger()` — evaluates whether to fire a proactive observation
  - `_idle_orchestrator()` — the main loop (silence timers, poke/nudge escalation)
  - `_send_hidden_turn()` — sends invisible context to Gemini
  - `_sanitize_tutor_output()` — strips internal control text
  - `_is_mid_session_restart_text()` — prevents tutor from re-greeting mid-session
- BUT: `_idle_orchestrator` references ~15 shared state variables (metrics dict, flags like `assistant_speaking`, `client_speech_active`, `camera_active`, etc.) that are defined in `websocket_endpoint`

**What to extract:**
- The 5 named functions above
- The idle orchestrator config constants (thresholds)
- The system prompt additions for proactive vision + goal-setting flow

**Refactoring needed:**
1. `_idle_orchestrator` needs its state dependencies documented (a clear list of what shared variables it reads/writes)
2. `_check_proactive_trigger` is clean — already takes explicit parameters
3. The config constants at the top of the file are already well-organized

**Hook points needed:**
- `on_video_frame` — update `last_video_frame_at`, `camera_active` flags
- `on_student_speech_start` / `on_student_speech_end` — track silence duration
- `on_tutor_audio_chunk` — update `assistant_speaking`, `last_tutor_audio_at`
- `on_turn_complete` — reset poke/nudge state
- `async_task` — idle orchestrator runs as a standalone `asyncio.create_task` alongside forwarders
- `on_tutor_text` — sanitize output (strip internal control markers)

**Transfer difficulty: MEDIUM** (functions exist but depend on shared state that must match the main app's state model)

---

### PoC 03 — Multilingual (1330 lines backend, 890 lines frontend)

**Core value:** Language contract system, language detection, confusion fallback, internal control messaging.

**Isolation status: ✅ BEST ISOLATED of all PoCs**
- Has **20+ pure utility functions** that take inputs and return outputs with no side effects:
  - `_language_label()`, `_language_short()`, `_default_language_policy()`, `_normalize_language_policy()`
  - `_build_profile_policy()`, `_build_language_contract()` — the contract builder
  - `_tokens()`, `_lang_score_from_tokens()`, `_detect_language()`, `_analyze_turn_language()`
  - `_is_confusion_signal()`, `_resolve_language_key()`, `_expected_language()`
  - `_build_internal_control()`, `_send_internal_control()`
- Plus event handlers: `_handle_student_transcript()`, `_finalize_tutor_turn()`

**What to extract:**
- ALL the utility functions above → can go straight into a `language.py` module
- The language contract text template
- The system prompt additions for multilingual rules
- The `_handle_student_transcript` and `_finalize_tutor_turn` hooks

**Refactoring needed:**
1. These functions are ALREADY almost ready to be a standalone module. Just group them into a `language.py` file
2. The two async handlers (`_handle_student_transcript`, `_finalize_tutor_turn`) need their callback signatures documented

**Hook points needed:**
- `on_student_transcript` — detect language, track confusion count, send internal control if wrong language
- `on_turn_complete` — finalize tutor turn (check tutor language compliance, send correction hidden turn if needed)
- `on_session_start` — build language contract from student profile, inject into system prompt context

**Transfer difficulty: LOW** — cleanest PoC, most modular code

---

### PoC 04 — Whiteboard Sync (961 lines backend, 1270 lines frontend)

**Core value:** write_notes tool dispatch, note normalization, deduplication, speech-synced whiteboard dispatcher, async queue.

**Isolation status: ⚠️ PARTIALLY ISOLATED**
- Core functions ARE named:
  - `_safe_text()`, `_normalize_note_type()`, `_normalize_title()`, `_normalize_content()` — note sanitization
  - `_inline_sentences_to_bullets()` — formatting
  - `_dedupe_key()` — prevent duplicate notes
  - `_dispatch_tool_call()` — handles write_notes tool execution
  - `_whiteboard_dispatcher()` — the async loop that times note delivery with speech
- BUT: `_whiteboard_dispatcher` is tightly coupled to the WebSocket send and playback state
- AND: `_dispatch_tool_call` handles write_notes AND other tools inline — not separated

**What to extract:**
- Note normalization utilities → `whiteboard.py`
- `_whiteboard_dispatcher` async loop (needs state interface defined)
- `_dispatch_tool_call` → the write_notes handling portion
- Frontend whiteboard rendering (note card DOM, CSS, scroll behavior)

**Refactoring needed:**
1. Split `_dispatch_tool_call` into tool-specific handlers: `_handle_write_notes()` separate from other tool routing
2. `_whiteboard_dispatcher` needs its dependencies made explicit (what queues, what state flags)
3. Note normalization functions are already pure — ready to move

**Hook points needed:**
- `on_tool_call("write_notes")` — normalize note fields before queueing (title, content, type, dedup)
- `on_tutor_audio_chunk` — dispatcher checks if tutor is speaking to time note delivery
- `on_turn_complete` — dispatcher flushes any queued notes
- `async_task` — whiteboard dispatcher runs as a standalone `asyncio.create_task`

**Transfer difficulty: MEDIUM** (normalization is easy, dispatcher coupling is the work)

---

### PoC 05 — Search Grounding (646 lines backend, 1333 lines frontend)

**Core value:** Google Search tool config, grounding metadata extraction, citation card UI.

**Isolation status: ✅ WELL ISOLATED**
- ONE core function does all the work:
  - `_extract_grounding(msg)` — parses Gemini response for grounding metadata, returns citation dicts
- Config is a single line: `tools=[types.Tool(google_search=types.GoogleSearch())]`
- System prompt additions are clearly documented in rules.md

**What to extract:**
- `_extract_grounding()` function → `grounding.py`
- Gemini config addition (tools list)
- System prompt grounding rules
- Frontend: citation toast component + "Verifying..." spinner

**Refactoring needed:**
- Almost none. `_extract_grounding` is already a pure function. Just needs to be importable.
- The citation card frontend code needs to be identified and marked in the HTML

**Hook points needed:**
- `on_gemini_message` — extract grounding metadata from Gemini response, forward citations to frontend
- `gemini_config` — add `google_search` to tools list

**Transfer difficulty: LOW** — cleanest extraction of all PoCs

---

### PoC 06 — Session Resilience (1151 lines backend, 1073 lines frontend)

**Core value:** Auto-reconnect with backoff, session state preservation, context injection on reconnect, Gemini 1011 handling.

**Isolation status: ⚠️ PARTIALLY ISOLATED**
- Has a proper `SessionState` CLASS — the best abstraction in any PoC:
  - Tracks student name, topic, language, transcript, reconnect count
  - Has methods for building resume context
- Core functions:
  - `_inject_resume_context()` — sends session state as hidden turn to new Gemini session
  - `_build_gemini_config()` — config factory (useful for reconnecting)
  - `_gemini_session_lifecycle()` — the retry loop for Gemini reconnects
  - `_receive_from_browser()` — replaces `_forward_browser_to_gemini` with reconnect awareness
- BUT: The entire WebSocket handler is restructured around the lifecycle concept — it's not a drop-in addition

**What to extract:**
- `SessionState` class → `session_state.py`
- `_inject_resume_context()` — standalone async function
- `_build_gemini_config()` — config factory
- Frontend reconnect manager (backoff logic, UI banners)

**Refactoring needed:**
1. `SessionState` is well-defined but the main app's `websocket_endpoint` would need restructuring to use it
2. The `_gemini_session_lifecycle` loop pattern needs to REPLACE the current main app's Gemini session pattern
3. Frontend reconnect logic needs clear `// === RECONNECT MODULE ===` markers

**Hook points needed:**
- `on_gemini_disconnect` — trigger reconnect with backoff, inject resume context into new session
- `on_session_start` — initialize `SessionState`, load resume context if reconnecting
- `on_session_end` — persist session state for potential future reconnect
- `gemini_lifecycle` — wraps the entire Gemini session in a retry loop (structural, not a simple hook)

**Transfer difficulty: HIGH** — requires restructuring the main app's session flow, not just adding functions

---

### PoC 07 — Latency Instrumentation (883 lines backend, 2106 lines frontend)

**Core value:** LatencyStats class, response/interruption timing, HUD overlay, budget alerts.

**Isolation status: ✅ WELL ISOLATED**
- Has a proper `LatencyStats` CLASS — self-contained stat tracker:
  - `record()`, `current`, `avg`, `p95`, `min_val`, `max_val`
  - No external dependencies
- Core functions:
  - `_build_summary()` — creates exportable stats table
  - `_send_latency_event()` — pushes individual measurements to frontend
  - `_send_latency_report()` — pushes aggregated report on turn_complete
- Timing logic is inline in forwarders (timestamp at speech_end → timestamp at first audio → compute delta)

**What to extract:**
- `LatencyStats` class → `latency.py`
- `_build_summary()`, `_send_latency_event()`, `_send_latency_report()`
- Timestamp injection points in forwarders (documented as comments)
- Frontend: HUD component + export button

**Refactoring needed:**
1. `LatencyStats` is already modular — just move it
2. The timestamp injection points in `_forward_browser_to_gemini` and `_forward_gemini_to_browser` need to be documented as hooks
3. Frontend HUD is self-contained but large (2106 lines total HTML) — need to identify the HUD-specific portion

**Hook points needed:**
- `on_student_speech_end` — record timestamp (start of latency measurement)
- `on_tutor_audio_chunk` (first chunk) — record timestamp (end of latency measurement), compute delta
- `on_turn_complete` — send aggregated latency report to frontend
- `on_interrupted` — record interruption-to-silence latency

**Transfer difficulty: LOW-MEDIUM** (class is clean; challenge is adding timestamp hooks in the right places)

---

### PoC 09 — Safety & Scope Guardrails (1056 lines backend, 996 lines frontend)

**Core value:** Input/output guardrail checking, answer leak detection, hidden turn reinforcement, sanitization.

**Isolation status: ⚠️ PARTIALLY ISOLATED**
- Core functions ARE named and mostly pure:
  - `_check_student_input_guardrails(text)` — pattern matching for off-topic, cheat, inappropriate
  - `_check_tutor_output_guardrails(text)` — answer leak detection
  - `_sanitize_tutor_output(text)` — strips internal control text
  - `_record_guardrail_event()` — logs guardrail triggers
  - `_send_hidden_turn()` — reinforce model behavior
- Reinforcement prompt templates are string constants (SOCRATIC_REINFORCE_PROMPT, etc.)

**What to extract:**
- All 5 functions above → `guardrails.py`
- Reinforcement prompt templates
- System prompt safety rules
- The check-and-reinforce hook pattern (call in forwarders)

**Refactoring needed:**
1. Functions are already fairly pure. `_check_student_input_guardrails` and `_check_tutor_output_guardrails` are standalone
2. `_record_guardrail_event` takes too many params (metrics, slog, websocket, event, source) — could use a GuardrailContext object
3. Reinforcement prompts should be grouped with the functions, not scattered in constants

**Hook points needed:**
- `on_student_transcript` — check input guardrails (off-topic, cheat, inappropriate), send reinforcement hidden turn if triggered
- `on_tutor_text` — check output guardrails (answer leak detection), sanitize internal control text
- `on_tutor_text` — strip internal markers before sending to frontend

**Transfer difficulty: LOW-MEDIUM** (functions are clean; integration is about inserting checks at the right hook points)

---

## Summary Table

| PoC | Core Lines | Unique Functions | Isolation | Transfer Difficulty | Primary Hook Points |
|-----|-----------|-----------------|-----------|--------------------|--------------------|
| 01 Interruption | ~200 | 0 (inline logic) | ❌ Not isolated | Medium | `on_interrupted`, `gemini_config`, frontend audio pipeline |
| 02 Proactive Vision | ~350 | 5 named | ⚠️ Partial | Medium | `on_video_frame`, `on_student_speech_*`, `on_turn_complete`, `async_task` |
| 03 Multilingual | ~500 | 20+ pure functions | ✅ Best | Low | `on_student_transcript`, `on_turn_complete`, `on_session_start` |
| 04 Whiteboard | ~350 | 8 named | ⚠️ Partial | Medium | `on_tool_call("write_notes")`, `on_tutor_audio_chunk`, `async_task` |
| 05 Search Grounding | ~100 | 1 core function | ✅ Well isolated | Low | `on_gemini_message`, `gemini_config` |
| 06 Session Resilience | ~400 | 4 + SessionState class | ⚠️ Partial | HIGH | `on_gemini_disconnect`, `gemini_lifecycle` (structural) |
| 07 Latency | ~200 | 4 + LatencyStats class | ✅ Well isolated | Low-Medium | `on_student_speech_end`, `on_tutor_audio_chunk`, `on_turn_complete` |
| 09 Safety Guardrails | ~250 | 5 named | ⚠️ Partial | Low-Medium | `on_student_transcript`, `on_tutor_text` |

---

## Hook Point Reference — Which PoCs Fire Where

This table is architecture-agnostic: it shows *when* each capability's logic must run, regardless of whether the main app uses raw `google-genai`, ADK, or any other plumbing.

| Hook Point | When it fires | PoCs that use it |
|---|---|---|
| `gemini_config` | Session config before Gemini connect | 01 (VAD sensitivity), 05 (search tool) |
| `on_session_start` | After WebSocket accepted, before first audio | 03 (build language contract), 06 (init SessionState) |
| `on_student_audio` | Each audio chunk from browser | 07 (timestamp for latency start) |
| `on_student_speech_start` | VAD detects speech begin | 02 (reset silence timer) |
| `on_student_speech_end` | VAD detects speech end | 02 (start silence timer), 07 (record latency start) |
| `on_student_transcript` | Student speech-to-text arrives | 03 (detect language), 09 (input guardrails) |
| `on_video_frame` | Camera or screen JPEG arrives | 02 (update camera_active, last_frame_at) |
| `on_tutor_audio_chunk` | Each audio chunk from Gemini | 04 (dispatcher timing), 07 (first chunk = latency end) |
| `on_tutor_text` | Tutor transcript text arrives | 02 (sanitize), 09 (output guardrails + sanitize) |
| `on_turn_complete` | Gemini signals turn finished | 02 (reset poke state), 03 (finalize turn language), 04 (flush notes), 07 (send report) |
| `on_interrupted` | Gemini signals barge-in | 01 (stale filter), 07 (interruption latency) |
| `on_tool_call` | Tool dispatch (by name) | 04 (`write_notes` normalization) |
| `on_gemini_message` | Every raw Gemini response | 05 (extract grounding metadata) |
| `on_gemini_disconnect` | Gemini session drops | 06 (reconnect with backoff) |
| `on_session_end` | WebSocket closing | 06 (persist state), 07 (final report) |
| `async_task` | Standalone loop alongside forwarders | 02 (idle orchestrator), 04 (whiteboard dispatcher) |

**Key insight:** `on_student_transcript`, `on_turn_complete`, and `on_tutor_text` are the most crowded hook points — 3-4 PoCs each. The integration order should ensure earlier-integrated PoCs don't block later ones at these shared points.

---

## The Big Picture: What Needs to Change Before Integration

### Problem 1: Every PoC copies the full WebSocket skeleton
Each PoC has its own `websocket_endpoint`, `_forward_browser_to_gemini`, and `_forward_gemini_to_browser`. The core capability code lives INSIDE these copied functions — not as standalone modules. This means you can't just import a PoC's capability; you have to diff its forwarder against the main app's forwarder and merge the deltas.

**Fix:** Before integration, extract each PoC's unique logic into standalone functions/modules with clear interfaces. The main app's forwarders become the ONE place where capabilities are orchestrated (calling into these modules at the right moments).

### Problem 2: Shared state is implicit
Each PoC's core logic references shared variables (metrics dicts, flags like `assistant_speaking`, timestamps, queues) that are defined ad-hoc in `websocket_endpoint`. There's no shared state object — each PoC invents its own set of flags.

**Fix:** Define a unified `SessionRuntime` class (or similar) in the main app that holds ALL the state any capability might need. Each module's functions take this runtime object as a parameter.

### Problem 3: Hook points aren't standardized
Each capability needs to run at specific moments: "when student audio arrives", "when tutor audio is about to be sent", "on turn_complete", "on interrupted", etc. But each PoC inserts its logic at slightly different points in the forwarder copy.

**Fix:** Define clear hook points in the main app's forwarders where capabilities can plug in. Something like:
- `on_student_audio(chunk, runtime)`
- `on_student_speech_start(runtime)`
- `on_student_speech_end(runtime)`
- `on_tutor_audio_chunk(chunk, runtime)`
- `on_turn_complete(runtime)`
- `on_interrupted(runtime)`
- `on_tool_call(name, args, runtime)`
- `on_session_start(runtime)`
- `on_session_end(runtime)`

### Problem 4: Frontend capabilities are inline in monolith HTML
Each PoC's frontend has its capability code mixed into one massive `index.html`. There's no module boundary — VAD code, whiteboard rendering, citation toasts, latency HUD, reconnect logic are all inline.

**Fix:** In the main app's frontend, organize JS into clearly marked sections with documented entry points. Each capability gets a `// === CAPABILITY: INTERRUPTION ===` block with its initialization, handlers, and state.

---

## Recommended Refactoring Order (Before Integration)

### Phase 1: Extract modules from PoCs that are ready
1. **PoC 03 (Multilingual)** → `language.py` — 20+ pure functions, almost no coupling
2. **PoC 05 (Search Grounding)** → `grounding.py` — 1 core function, clean extraction
3. **PoC 07 (Latency)** → `latency.py` — LatencyStats class + helpers, self-contained
4. **PoC 09 (Safety)** → `guardrails.py` — 5 functions + prompt templates

### Phase 2: Refactor PoCs that need work
5. **PoC 04 (Whiteboard)** → `whiteboard.py` — split tool dispatch, extract note normalization
6. **PoC 02 (Proactive Vision)** → `idle_orchestrator.py` — document state deps, extract trigger logic
7. **PoC 01 (Interruption)** → frontend module markers — extract VAD/gate/barge-in blocks

### Phase 3: Restructure main app for integration
8. **PoC 06 (Session Resilience)** → Requires restructuring main app's session lifecycle. Do last because it changes the skeleton that everything else plugs into.

---

## What I Recommend You Do NOT Do

1. **Don't create the modules in the PoC directories** — create them directly in `backend/` as new files that the main app will import
2. **Don't try to make PoCs backward-compatible** — once a module is extracted, the PoC can keep its monolith for reference but the module is the source of truth
3. **Don't extract frontend modules as separate JS files** — for a hackathon, marked sections in one HTML file is fine. Module bundling is overkill.
4. **Don't refactor all 8 before starting integration** — extract Phase 1 (the easy ones), integrate them into the main app, THEN do Phase 2. Seeing the first modules working in the main app will inform how to extract the harder ones.
