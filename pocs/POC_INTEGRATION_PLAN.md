# PoC Integration Plan v3

**Goal:** Build the core product by integrating one PoC at a time, validating each with measurable metrics before moving on.

**Architecture (three phases):**
- **Phase 0 — Isolate:** Extract core logic from each PoC's monolith into standalone modules *within the PoC directory*. Each PoC still runs on its own — the module is validated by importing it back into the PoC's `main.py`.
- **Phase 1 — Backend:** Move the isolated modules into the main `backend/`. Wire them into the ADK skeleton. Validate each step using PoC frontends as temporary test harnesses.
- **Phase 2 — Frontend:** Build one unified `index.html` that wires up all backend capabilities.

**Why Phase 0 exists:**

Every PoC is a standalone monolith (400–1300 lines). The unique logic is buried inside copied boilerplate (`websocket_endpoint`, `_forward_browser_to_gemini`, `_forward_gemini_to_browser`). Without Phase 0, integration means doing two things at once — surgical extraction AND wiring into a new architecture. That's risky.

Phase 0 separates the concerns:
1. **Extract** — pull core logic into an importable module, inside the PoC
2. **Validate** — PoC still runs, module is tested in its original context
3. **Integrate** (Phase 1) — move the already-validated module into `backend/`, wire it up

The easy PoCs (03, 05, 07) take 15–30 minutes each. The hard ones (06, 02, 04) take longer but the payoff is huge: Phase 1 becomes a wiring exercise, not a surgery.

**Foundation (reused, not rewritten):**
- `tutor_agent/agent.py` — System prompt, phase state machine, 7 tool functions, Firestore persistence. Already clean and modular. Tool functions are refactored to use ADK's `ToolContext` (minor signature change) but logic stays identical.
- `gemini_live.py` — Whiteboard/topic queue registries are kept. The `GeminiLiveSession` class is **replaced** by ADK's `Runner.run_live()`.

**Critical decision — ADK goes in Step 0, BEFORE any PoC integration:**

ADK changes the **plumbing** — how messages flow between browser, backend, and Gemini. Every PoC integration plugs into that plumbing: hidden turns, tool dispatch, event processing, state access. If we build on raw `client.aio.live.connect()` first and add ADK later, we'd have to rewire every hook point a second time.

Specifically, without ADK first:
- Every `await session.send_realtime_input(audio=...)` → would need rewriting to `queue.send_realtime(blob)`
- Every `await session.send_client_content(turns=...)` (hidden turns) → would need rewriting to `queue.send_content(content)`
- Every tool function with `state: dict` → would need refactoring to `tool_context: ToolContext`
- Every `async for msg in session.receive()` → would need replacing with `async for event in runner.run_live()`
- Every manual `_dispatch_tool()` call → would disappear (ADK auto-dispatches)

That's touching validated code for zero feature gain — pure rework. By doing ADK first in Step 0, every PoC integration from Step 1 onward uses the right API from day one. No rewiring.

---

## Phase 0 — Isolate PoC Backend Modules

> **Rule:** Each PoC's `main.py` keeps working after extraction. The module is created *inside* the PoC directory, and `main.py` is updated to import from it. If the PoC still passes its test scenarios → the module is validated.

### Phase 0 Order (easy → hard)

Do the well-isolated PoCs first. They're fast, build confidence, and establish the pattern.

---

### P0.1 — PoC 05: Search Grounding → `grounding.py`

**Difficulty: TRIVIAL** | **Time: 15 min**

**Create:** `pocs/05_search_grounding/grounding.py`

| Extract | Lines | Notes |
|---|---|---|
| `_extract_grounding(msg)` | 165–234 | Pure function. Takes Gemini message, returns list of citation dicts. Zero state dependencies. |

**Update `main.py`:** Replace inline `_extract_grounding` with `from grounding import extract_grounding`.

**Validate:** Run PoC 05, ask a factual question ("What is the capital of France?"), confirm citations still appear.

---

### P0.2 — PoC 07: Latency → `latency.py`

**Difficulty: LOW** | **Time: 20 min**

**Create:** `pocs/07_latency_instrumentation_and_budget/latency.py`

| Extract | Lines | Notes |
|---|---|---|
| `class LatencyStats` | 102–149 | Self-contained. `record()`, `stats()`, `is_alert()`. No external deps. |
| `_build_summary(trackers)` | 372–385 | Takes dict of LatencyStats, returns summary dict. |
| `_send_latency_event(...)` | 412–461 | Async. Records measurement, sends to client, checks budget. Needs `websocket` + `slog` params. |
| `_send_latency_report(...)` | 464–487 | Async. Sends full report on turn_complete. |

**Update `main.py`:** Import class + helpers from `latency.py`. Timestamp hooks stay inline in forwarders (they're 1-line `time.time()` calls).

**Validate:** Run PoC 07, have a conversation, confirm latency HUD still shows numbers.

---

### P0.3 — PoC 03: Multilingual → `language.py`

**Difficulty: LOW** | **Time: 30 min**

**Create:** `pocs/03_multilingual/language.py`

| Extract | Lines | Notes |
|---|---|---|
| `_language_label()` | 280–288 | Pure |
| `_language_short()` | 291–299 | Pure |
| `_default_language_policy()` | 302–316 | Pure |
| `_normalize_language_policy()` | 319–364 | Pure |
| `_build_profile_policy()` | 367–376 | Pure |
| `_build_language_contract()` | 379–430 | Pure — the key function. Generates system prompt language contract. |
| `_tokens()` | 436–437 | Pure |
| `_lang_score_from_tokens()` | 440–454 | Pure |
| `_detect_language()` | 457–475 | Pure |
| `_analyze_turn_language()` | 478–535 | Pure |
| `_is_confusion_signal()` | 538–542 | Pure |
| `_resolve_language_key()` | 548–561 | Pure |
| `_expected_language()` | 564–582 | Pure |
| `_build_internal_control()` | 585–614 | Pure |
| `_send_internal_control()` | 617–653 | Async — sends hidden turn. Needs session + websocket params. |
| `_handle_student_transcript()` | 1076–1180 | Async hook — analyzes student speech. Needs metrics + state params. |
| `_finalize_tutor_turn()` | 1183–1298 | Async hook — evaluates tutor language compliance. Needs metrics + state params. |

**Interface pattern:** Pure functions have no dependencies. The two async hooks (`_handle_student_transcript`, `_finalize_tutor_turn`) need a `LanguageRuntime` dataclass or dict with: `metrics`, `language_policy`, `slog`, `websocket`, `session`.

**Update `main.py`:** Import all functions. The forwarders call `handle_student_transcript()` and `finalize_tutor_turn()` at the right moments.

**Validate:** Run PoC 03, speak in Portuguese, confirm language detection and contract enforcement still work.

---

### P0.4 — PoC 09: Safety Guardrails → `guardrails.py`

**Difficulty: LOW-MEDIUM** | **Time: 30 min**

**Create:** `pocs/09_safety_scope_guardrails/guardrails.py`

| Extract | Lines | Notes |
|---|---|---|
| `_sanitize_tutor_output(text)` | 289–309 | Pure. Returns `(cleaned_text, had_internal)`. |
| `_check_student_input_guardrails(text)` | 399–427 | Pure. Returns list of guardrail event dicts. |
| `_check_tutor_output_guardrails(text)` | 430–444 | Pure. Returns list of guardrail event dicts. |
| `_record_guardrail_event(...)` | 737–782 | Async. Logs event + sends to frontend. Needs metrics + websocket + slog. |
| `_send_hidden_turn(session, text)` | 385–393 | Async. Sends reinforcement prompt. |
| Reinforcement prompt constants | scattered | `SOCRATIC_REINFORCE_PROMPT`, `SCOPE_REINFORCE_PROMPT`, `CAMERA_UNCLEAR_REINFORCE_PROMPT` — group them in the module. |

**Update `main.py`:** Import functions. Guardrail checks are called in forwarders: input check on student transcript, output check on tutor text.

**Validate:** Run PoC 09, try "just tell me the answer" → confirm guardrail fires and reinforcement is sent.

---

### P0.5 — PoC 04: Whiteboard → `whiteboard.py`

**Difficulty: MEDIUM** | **Time: 45 min**

**Create:** `pocs/04_whiteboard_sync/whiteboard.py`

| Extract | Lines | Notes |
|---|---|---|
| `_safe_text(v)` | 204–205 | Pure |
| `_normalize_note_type(t)` | 208–212 | Pure |
| `_normalize_title(t)` | 215–221 | Pure |
| `_normalize_content(c)` | 236–273 | Pure |
| `_inline_sentences_to_bullets(t)` | 224–233 | Pure |
| `_dedupe_key(title, content)` | 276–279 | Pure |
| `handle_write_notes_tool(args, dedupe_keys, metrics, slog)` | 726–833 | Extracted from `_dispatch_tool_call`. Only the `write_notes` handling. Returns `(note_dict, tool_response)`. Needs `dedupe_keys` set + `metrics` dict. |
| `whiteboard_dispatcher(wb_queue, send_json, runtime, metrics, slog)` | 839–929 | Async loop. Reads from queue, dispatches synced with speech. Needs `runtime["assistant_speaking"]`, `runtime["client_tutor_playing"]`, `runtime["last_video_frame_at"]`. |

**Key refactoring:** Split `_dispatch_tool_call` (lines 726–833) so that the `write_notes` branch is extracted as `handle_write_notes_tool()`. The original function becomes a thin router that calls the extracted function.

**Update `main.py`:** Import note normalization functions + dispatcher. `_dispatch_tool_call` calls `handle_write_notes_tool()` for `write_notes` and handles other tools itself.

**Validate:** Run PoC 04, ask tutor to explain something → confirm whiteboard notes appear with correct formatting and timing.

---

### P0.6 — PoC 02: Proactive Vision → `proactive.py`

**Difficulty: MEDIUM** | **Time: 45 min**

**Create:** `pocs/02_proactive_vision/proactive.py`

| Extract | Lines | Notes |
|---|---|---|
| Config constants | 58–84 | `ORGANIC_POKE_THRESHOLD_S`, `HARD_NUDGE_THRESHOLD_S`, `CHECK_INTERVAL_S`, `CAMERA_ACTIVE_TIMEOUT_S`, `POKE_RESPONSE_GRACE_S`, etc. |
| `_sanitize_tutor_output(text)` | 220–242 | Pure. Returns `(cleaned, had_internal)`. |
| `_is_mid_session_restart_text(text, turn_completes)` | 244–251 | Pure. |
| `_send_hidden_turn(session, text)` | 1015–1024 | Async. |
| `_check_proactive_trigger(now, metrics, slog, websocket)` | 938–1010 | Async. Evaluates if tutor should proactively speak. Reads ~6 metrics keys. |
| `_idle_orchestrator(websocket, session, session_id, metrics, slog)` | 1026–1171 | Async loop. The core orchestrator. Reads/writes ~15 metrics keys. |
| Hidden prompt templates | in SYSTEM_PROMPT area | `IDLE_POKE_PROMPT`, `IDLE_NUDGE_PROMPT`, etc. |

**State interface:** The orchestrator needs a `ProactiveRuntime` with these keys:

```python
# READ by orchestrator/trigger
"tutor_speaking", "client_tutor_playing", "student_speaking",
"last_student_speech_at", "last_student_stale_reset_at",
"last_video_frame_at", "silence_started_at",
"idle_poke_sent", "idle_nudge_sent", "last_poke_at",
"has_seen_tutor_turn_complete", "last_nudge_at",
"last_hidden_prompt_at", "last_tutor_output_at"

# WRITTEN by orchestrator/trigger
"student_speaking", "silence_started_at", "idle_poke_sent",
"idle_nudge_sent", "backend_pokes", "last_poke_at",
"backend_nudges", "last_nudge_at", "last_hidden_prompt_at",
"proactive_triggers", "silence_durations_s",
"nudge_triggers", "organic_triggers", "false_positives"
```

**Update `main.py`:** Import orchestrator + trigger + helpers. Orchestrator is launched as an `asyncio.create_task()` alongside the forwarders.

**Validate:** Run PoC 02, point camera at homework, stay silent for 6+ seconds → confirm tutor proactively comments.

---

### P0.7 — PoC 10: Screen Share Toggle → `screen_share.py`

**Difficulty: MEDIUM** | **Time: 30 min**

**Create:** `pocs/10_TODO_screen_share_toggle/screen_share.py`

| Extract | Lines | Notes |
|---|---|---|
| `SOURCE_SWITCH_COOLDOWN_S` | 80 | Config constant |
| `SOURCE_SWITCH_TO_SCREEN_PROMPT` | 164–171 | Hidden prompt template |
| `SOURCE_SWITCH_TO_CAMERA_PROMPT` | 173–180 | Hidden prompt template |
| `STOP_SHARING_PROMPT` | 182–188 | Hidden prompt template |
| `handle_source_switch(msg, session, metrics, slog)` | 522–587 | Extracted from forwarder. Debounce + hidden turn injection. |
| `handle_stop_sharing(msg, session, metrics, slog)` | 589–620 | Extracted from forwarder. |
| `_is_visual_active(now, metrics)` | 937–947 | Pure. Returns `camera_active or screen_active`. |

**Note:** `screen_frame` handling is 1 line (forward JPEG to Gemini) — stays inline, not worth extracting.

**Update `main.py`:** Import handlers. Forwarder calls `handle_source_switch()` / `handle_stop_sharing()` on matching message types.

**Validate:** Run PoC 10, toggle camera ↔ screen share 5 times → confirm tutor acknowledges each switch, no reconnects, audio continues.

---

### P0.8 — PoC 06: Session Resilience → `session_state.py`

**Difficulty: HIGH** | **Time: 60 min**

**Create:** `pocs/06_session_resilience/session_state.py`

| Extract | Lines | Notes |
|---|---|---|
| `class SessionState` | 207–351 | 14 attributes, 7 methods. Self-contained except `build_resume_context()` returns a string. |
| `_inject_resume_context(session, state, metrics, slog, *, source, force)` | 381–423 | Async. Calls `state.build_resume_context()` + `_send_hidden_turn()`. |
| `_build_gemini_config()` | 429–453 | Pure factory. Returns `LiveConnectConfig`. |
| `_send_hidden_turn(session, text)` | 370–378 | Async. |

**What stays in `main.py`:**
- `_gemini_session_lifecycle()` (lines 742–956) — this IS the lifecycle, not a utility. It orchestrates connect/reconnect using the extracted pieces.
- `_receive_from_browser()` (lines 555–736) — WebSocket handler, uses `SessionState` methods.
- `gemini_holder` dict and `gemini_ready_event` / `shutdown_event` — session-level coordination.

**Why partial extraction:** The lifecycle and receiver are tightly coupled to the WebSocket endpoint. Extracting `SessionState` + `_inject_resume_context` + `_build_gemini_config` is enough — Phase 1 will restructure the lifecycle around ADK's `Runner.run_live()` anyway.

**Update `main.py`:** Import `SessionState`, `inject_resume_context`, `build_gemini_config`. The lifecycle and receiver use them via import.

**Validate:** Run PoC 06, trigger a simulate_disconnect → confirm tutor resumes context without re-introducing itself.

---

### P0.9 — PoC 01: Interruption Handling → config only

**Difficulty: TRIVIAL** | **Time: 10 min** | **No module created**

PoC 01's backend is config-only — no unique functions to extract. The core interruption logic (Silero VAD, audio gate, barge-in) is entirely in the frontend.

**What to document (for Phase 1):**
- VAD config values: `START_SENSITIVITY_LOW`, `END_SENSITIVITY_LOW`, `silence_duration_ms: 700`, `prefix_padding_ms: 300`
- Stale interrupt filter: `if not assistant_speaking: ignore interrupted event`
- These go directly into `RunConfig` in Step 0/Step 2

**No Phase 0 action needed.** Just note the config values for Phase 1.

---

### Phase 0 Summary

| # | PoC | Module created | Difficulty | Est. time |
|---|---|---|---|---|
| P0.1 | 05 Search Grounding | `grounding.py` | Trivial | 15 min |
| P0.2 | 07 Latency | `latency.py` | Low | 20 min |
| P0.3 | 03 Multilingual | `language.py` | Low | 30 min |
| P0.4 | 09 Safety Guardrails | `guardrails.py` | Low-Med | 30 min |
| P0.5 | 04 Whiteboard | `whiteboard.py` | Medium | 45 min |
| P0.6 | 02 Proactive Vision | `proactive.py` | Medium | 45 min |
| P0.7 | 10 Screen Share | `screen_share.py` | Medium | 30 min |
| P0.8 | 06 Session Resilience | `session_state.py` | High | 60 min |
| P0.9 | 01 Interruption | (none — config only) | Trivial | 10 min |

**Total Phase 0 estimate: ~4.5 hours**

**Validation rule:** After each extraction, run the PoC's own test scenarios. If it still passes → module is correct. If it breaks → fix before moving on.

---

## Metrics Strategy

Each step produces a **metrics report** logged at session end. Format:

```json
{
  "step": "01_interruption",
  "session_id": "...",
  "timestamp": "...",
  "metrics": {
    "false_interruption_rate": 0,
    "barge_in_latency_ms_avg": 210,
    "self_interruption_count": 0,
    "student_heard_rate": 1.0,
    "mid_word_cutoffs": 0
  },
  "pass": true,
  "failures": []
}
```

**How it works:**
1. Each step defines its metrics (from the PoC's prd.md targets)
2. During testing, metrics are collected via `LatencyStats`-style counters in session state
3. At session end (`/session_report` endpoint or WS message), backend computes pass/fail against PRD thresholds
4. Compare with PoC baseline: run same test scenario on PoC → run on main app → metrics must match or beat

**Shared metrics infrastructure** (added in Step 0, used by all steps):

```python
class MetricsCollector:
    """Collects per-PoC metrics during a session."""
    def __init__(self, step_name: str):
        self.step = step_name
        self.counters: dict[str, int] = {}
        self.timers: dict[str, list[float]] = {}
        self.flags: dict[str, bool] = {}

    def inc(self, name: str, by: int = 1): ...
    def record_ms(self, name: str, value_ms: float): ...
    def set_flag(self, name: str, value: bool): ...
    def report(self, thresholds: dict) -> dict: ...  # returns pass/fail with details
```

---

## Step 0 — ADK Skeleton + Lean Pipe

**What:** Replace the raw Gemini Live API usage with ADK's `Runner` + `Agent` + `LiveRequestQueue` pattern, wrapped in a FastAPI WebSocket endpoint. No PoC features yet — just the pipe.

**Why ADK first (not later):**

ADK changes how EVERYTHING flows. Every subsequent PoC integration touches these APIs:

| Operation | Without ADK (raw API) | With ADK |
|---|---|---|
| Send audio to Gemini | `await session.send_realtime_input(audio=Blob(...))` | `queue.send_realtime(Blob(...))` |
| Send video to Gemini | `await session.send_realtime_input(video=Blob(...))` | `queue.send_realtime(Blob(...))` |
| Send hidden turn | `await session.send_client_content(turns=Content(...), turn_complete=True)` | `queue.send_content(Content(...))` |
| Receive events | `async for msg in session.receive()` | `async for event in runner.run_live(...)` |
| Tool dispatch | Manual: `fn = TOOL_FUNCTIONS[name]; result = await fn(**args, state=state)` then `await session.send_tool_response(...)` | **Automatic** — ADK detects tool calls, executes functions, sends responses |
| Tool state access | `def my_tool(arg, *, state: dict)` reads `state["key"]` | `def my_tool(arg, tool_context: ToolContext)` reads `tool_context.state["key"]` |
| Tool declarations | Manual `_build_tool_declarations()` building `FunctionDeclaration` objects | **Automatic** — ADK introspects function signatures via `FunctionTool` |
| Session config | `types.LiveConnectConfig(response_modalities=..., speech_config=..., ...)` | `RunConfig(streaming_mode=StreamingMode.BIDI, response_modalities=..., ...)` |
| Session lifecycle | `async with client.aio.live.connect(model, config) as session:` | `runner.run_live(user_id, session_id, queue, run_config)` |
| Guardrails | Ad-hoc `if` checks in forwarding functions | ADK callbacks: `before_model_call`, `after_model_call`, `before_tool_call` |
| Activity signals | `await session.send_realtime_input(activity_start=ActivityStart())` | `queue.send_realtime(activity_start=ActivityStart())` |

If you integrate PoCs on raw API first, then add ADK, you rewrite EVERY line in the table above across ALL integrated PoCs. That's pure rework on validated code — risk for zero feature gain.

By doing ADK in Step 0, every PoC from Step 1 onward uses the right API from day one.

**Why ADK matters for the hackathon:**
- Judging criteria: "Effective Google Cloud utilization; sound agent logic" (30% weight)
- ADK is Google's agent framework — using it shows deep alignment with their stack
- `Runner` handles tool dispatch automatically (eliminates ~80 lines of manual dispatch code)
- `ToolContext` gives tools clean state access (no more `**kwargs` / `state: dict` injection)
- Session management via `SessionService` (InMemory for dev → DatabaseSessionService for prod)
- ADK callbacks for guardrails = the "Responsible AI" pattern Google promotes (Step 8)

### Architecture change

**Before (current `gemini_live.py`):**
```
Browser ──WebSocket──▶ main.py ──▶ GeminiLiveSession ──▶ Gemini Live API
                                    (raw client.aio.live.connect)
                                         │
                                    manual _dispatch_tool()
                                         │
                                    tutor_agent/agent.py
                                    (tool functions + SYSTEM_PROMPT)
```

**After (ADK):**
```
Browser ──WebSocket──▶ main.py ──▶ Runner.run_live(LiveRequestQueue) ──▶ Gemini Live API
                                         │
                                    AUTOMATIC tool dispatch
                                         │
                                    Agent(tools=[FunctionTool(...)], instruction=SYSTEM_PROMPT)
                                         │
                                    callbacks: before_model_call / after_model_call
                                    (guardrails in Step 8)
```

### What to build

#### 1. `backend/agent.py` — ADK Agent definition

Replaces `tutor_agent/agent.py` structure. Same tool logic, new signatures.

```python
from google.adk.agents import Agent
from google.adk.tools import FunctionTool, ToolContext, google_search
from google.genai import types

# ---------------------------------------------------------------------------
# Tool functions — same logic as current agent.py, new signature
# ---------------------------------------------------------------------------

# BEFORE (current):
#   def set_session_phase(phase: str, *, state: dict, **kwargs) -> dict:
#       current = state.get("session_phase", "greeting")
#       state["session_phase"] = normalized
#
# AFTER (ADK):
def set_session_phase(phase: str, tool_context: ToolContext) -> dict:
    """Transition the tutoring session to a new phase."""
    state = tool_context.state  # <-- same dict, accessed through ToolContext
    current = state.get("session_phase", "greeting")
    # ... rest of logic is IDENTICAL
    state["session_phase"] = normalized
    return {"result": "transitioned", "current_phase": normalized, ...}

# BEFORE:
#   def write_notes(title, content, note_type, status, *, state, **kwargs):
#       session_id = state.get("session_id")
#
# AFTER:
def write_notes(title: str, content: str, note_type: str = "insight",
                status: str = "pending", tool_context: ToolContext = None) -> dict:
    """Write a note to the student's whiteboard."""
    state = tool_context.state
    session_id = state.get("session_id")
    # ... rest of logic is IDENTICAL

# Same pattern for: get_backlog_context, log_progress, set_checkpoint_decision,
# update_note_status, switch_topic

# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------
agent = Agent(
    name="seeme_tutor",
    model="gemini-live-2.5-flash-native-audio",
    tools=[
        set_session_phase,       # ADK auto-wraps Python functions as FunctionTool
        get_backlog_context,
        log_progress,
        set_checkpoint_decision,
        write_notes,
        update_note_status,
        switch_topic,
        google_search,           # Built-in ADK tool — replaces manual GOOGLE_SEARCH_TOOL
    ],
    instruction=SYSTEM_PROMPT,   # Same system prompt, same phase instructions
)
```

**Key refactoring pattern for ALL tools:**
```python
# Find:   def my_tool(arg1, arg2, *, state: dict, **kwargs) -> dict:
#             value = state.get("key")
#             state["key"] = new_value
#
# Replace: def my_tool(arg1: str, arg2: str, tool_context: ToolContext) -> dict:
#             value = tool_context.state.get("key")
#             tool_context.state["key"] = new_value
```

#### 2. `backend/main.py` — FastAPI + WebSocket + ADK streaming

```python
import asyncio
import base64
import json
import logging
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent import agent  # Our ADK Agent from agent.py

app = FastAPI()
logger = logging.getLogger("seeme_tutor")

# ---------------------------------------------------------------------------
# ADK setup — created ONCE at startup, shared across all sessions
# ---------------------------------------------------------------------------
session_service = InMemorySessionService()  # → DatabaseSessionService for prod
runner = Runner(
    app_name="seeme_tutor",
    agent=agent,
    session_service=session_service,
)

# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/{student_id}/{session_id}")
async def ws_endpoint(websocket: WebSocket, student_id: str, session_id: str):
    await websocket.accept()

    # Create LiveRequestQueue — the bridge between our WebSocket and ADK
    queue = LiveRequestQueue()

    # Configure streaming behavior
    run_config = RunConfig(
        streaming_mode=StreamingMode.BIDI,
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck"),
            ),
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_LOW,
                prefix_padding_ms=300,
                silence_duration_ms=700,
            ),
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )

    # --- UPSTREAM: Browser → LiveRequestQueue → Gemini ---
    async def upstream():
        try:
            while True:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
                msg_type = msg.get("type")

                if msg_type == "audio":
                    audio_bytes = base64.b64decode(msg["data"])
                    queue.send_realtime(
                        types.Blob(data=audio_bytes, mime_type="audio/pcm;rate=16000")
                    )

                elif msg_type in ("camera_frame", "screen_frame"):
                    jpeg_bytes = base64.b64decode(msg["data"])
                    queue.send_realtime(
                        types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                    )

                elif msg_type == "text":
                    content = types.Content(
                        parts=[types.Part(text=msg["data"])],
                        role="user",
                    )
                    queue.send_content(content)

                # Control messages (source_switch, speech_start, etc.)
                # → handled here, PoC-specific logic added in later steps

        except WebSocketDisconnect:
            pass

    # --- DOWNSTREAM: ADK events → Browser ---
    async def downstream():
        try:
            async for event in runner.run_live(
                user_id=student_id,
                session_id=session_id,
                live_request_queue=queue,
                run_config=run_config,
            ):
                # Audio output
                if hasattr(event, 'content') and event.content:
                    for part in event.content.parts or []:
                        if hasattr(part, 'inline_data') and part.inline_data:
                            encoded = base64.b64encode(part.inline_data.data).decode()
                            await websocket.send_text(json.dumps({
                                "type": "audio", "data": encoded
                            }))

                # Tool call events (ADK auto-dispatches, we just observe)
                if event.get_function_calls():
                    for fc in event.get_function_calls():
                        logger.info("TOOL_CALL: %s", fc.name)

                # Tool response events
                if event.get_function_responses():
                    for fr in event.get_function_responses():
                        logger.info("TOOL_RESPONSE: %s", fr.name)
                        # Whiteboard queue dispatch happens inside the tool itself

                # Transcripts, turn_complete, interrupted
                # → parsed from event, forwarded to browser as JSON

        except WebSocketDisconnect:
            pass

    try:
        await asyncio.gather(upstream(), downstream(), return_exceptions=True)
    finally:
        queue.close()
```

#### 3. Whiteboard/topic queue registries — kept from `gemini_live.py`

```python
# backend/queues.py (extracted from gemini_live.py — still needed)
import asyncio

_whiteboard_queues: dict[str, asyncio.Queue] = {}
_topic_update_queues: dict[str, asyncio.Queue] = {}

def register_whiteboard_queue(session_id: str) -> asyncio.Queue: ...
def get_whiteboard_queue(session_id: str) -> asyncio.Queue | None: ...
def unregister_whiteboard_queue(session_id: str) -> None: ...
# same for topic_update_queues
```

These are still needed because `write_notes` tool pushes to a queue, and the whiteboard dispatcher (Step 5) reads from it. ADK handles tool dispatch but doesn't replace our async queue pattern for frontend-synced delivery.

### Migration map — current file → new file

| Current file | What happens to it | New location |
|---|---|---|
| `tutor_agent/agent.py` | Tool functions refactored (ToolContext), prompt kept | `backend/agent.py` |
| `tutor_agent/__init__.py` | Deleted | — |
| `gemini_live.py` GeminiLiveSession | **Deleted** — replaced by `Runner.run_live()` | — |
| `gemini_live.py` queue registries | Extracted | `backend/queues.py` |
| `gemini_live.py` monkey patch | Likely not needed with ADK (test!) | — |
| `main.py` WebSocket endpoint | Rewritten with upstream/downstream pattern | `backend/main.py` |
| `main.py` `_forward_browser_to_gemini` | Becomes `upstream()` in new main.py | `backend/main.py` |
| `main.py` `_forward_gemini_to_browser` | Becomes `downstream()` in new main.py | `backend/main.py` |
| `main.py` `_idle_orchestrator` | Added in Step 3 (not in skeleton) | `backend/main.py` |

### What stays from current code
- Tool function **logic** (Firestore writes, phase transitions, whiteboard queueing) — identical
- `SYSTEM_PROMPT` and phase instructions — moved into `Agent(instruction=...)`
- Whiteboard/topic queue registries — extracted to `queues.py`
- `Dockerfile` and `deploy.sh` — unchanged

### What gets replaced
- `GeminiLiveSession` class → **deleted**, replaced by ADK `Runner.run_live()`
- Manual `_dispatch_tool()` in `gemini_live.py` → **deleted**, ADK auto-dispatches
- Manual `LiveConnectConfig` → replaced by ADK `RunConfig`
- `_build_tool_declarations()` → **deleted**, ADK introspects `FunctionTool` signatures
- `TOOL_FUNCTIONS` dict + `TOOL_DECLARATIONS` object → **deleted**, replaced by `Agent(tools=[...])`

### How PoC integrations use ADK APIs (preview)

Every subsequent step plugs into the ADK pipe. Here's how common PoC patterns map:

| PoC pattern | Raw API (current PoCs) | ADK (main app) |
|---|---|---|
| Hidden turn (proactive vision, source switch, language correction) | `await session.send_client_content(turns=Content(...), turn_complete=True)` | `queue.send_content(Content(parts=[Part(text=...)], role="user"))` |
| Send audio | `await session.send_realtime_input(audio=Blob(...))` | `queue.send_realtime(Blob(...))` |
| Send video frame | `await session.send_realtime_input(video=Blob(...))` | `queue.send_realtime(Blob(...))` |
| Activity start/end | `await session.send_realtime_input(activity_start=ActivityStart())` | `queue.send_realtime(activity_start=ActivityStart())` |
| Tool with state | `def my_tool(arg, *, state: dict, **kwargs)` | `def my_tool(arg: str, tool_context: ToolContext)` |
| Guardrail check | `if` checks scattered in forwarders | ADK callback: `before_model_call` / `after_model_call` |
| Read session state | `state["key"]` | `tool_context.state["key"]` (in tools) or `session.state["key"]` (in callbacks) |

### Dependencies

```
pip install google-adk google-genai fastapi uvicorn python-dotenv
```

### Pass gate

| Metric | Target | How to test |
|---|---|---|
| Audio round-trip | Speak → hear response | Open browser, talk, listen |
| Video frames arrive | Camera → backend logs show frames | Point camera, check logs |
| Tool dispatch works | Ask tutor to write a note → whiteboard queue receives it | Check queue after tool call |
| ADK events flow | `runner.run_live()` yields audio + text + tool events | Log events in downstream |
| MetricsCollector works | Session report endpoint returns JSON | Call `/session_report` |
| No manual dispatch | Zero calls to `_dispatch_tool` or `send_tool_response` | Grep codebase |

---

## Phase 1 — Backend Integration (all steps, backend only)

> For each step below, test using the corresponding PoC's `index.html` as a temporary frontend.
> Point the PoC frontend at the main app's WebSocket endpoint to validate.

---

### Step 1 — PoC 06: Session Resilience

**Why first:** This changes the WebSocket lifecycle (reconnect loop, state tracking, Gemini retry). Every other PoC plugs INTO this lifecycle.

#### What to extract

| Function / Class | What it does |
|---|---|
| `class SessionState` | Tracks student, topic, language, transcript, reconnect count |
| `_inject_resume_context()` | Sends session state as hidden turn after reconnect |
| `_build_gemini_config()` | Config factory for connect AND reconnects |
| `_gemini_session_lifecycle()` | Retry loop: connect → forward → on disconnect → retry with backoff |
| `_receive_from_browser()` | Reconnect-aware message handling |

#### ADK adaptation
- `SessionState` becomes initial state loaded into ADK session via `session_service`
- Reconnect logic wraps `runner.run_live()` in a retry loop
- `_inject_resume_context()` uses `queue.send_content()` to inject hidden turn after reconnect

#### Metrics (from PoC 06 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `reconnect_success_rate` | 100% for transient drops | >= 1.0 |
| `reconnect_latency_ms` | < 2000ms | <= 2000 |
| `context_preserved` | Tutor never re-introduces | true |
| `graceful_end_on_exhaustion` | Clean "session ended" message | true |

---

### Step 2 — PoC 01: Interruption Handling

**Why second:** Audio quality is the foundation. Proactive vision needs to know silence vs. noise. Whiteboard needs clean turn boundaries.

#### What to extract

**Backend (minimal):**

| Change | What |
|---|---|
| VAD sensitivity config | `START_SENSITIVITY_LOW`, `END_SENSITIVITY_LOW`, `silence_duration_ms: 700` |
| Stale interrupt filter | Ignore `interrupted` events when `assistant_speaking` is False |

**Note:** The bulk of interruption handling is frontend (Silero VAD, audio gate, barge-in confirmation). Backend changes are config-only. Frontend work deferred to Phase 2.

#### ADK adaptation
- VAD config goes into `RunConfig` via `realtime_input_config`
- Interrupt filter added to the downstream event processing loop

#### Metrics (from PoC 01 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `false_interruption_count` | 0 per 5-min session | <= 0 |
| `barge_in_latency_ms_avg` | < 300ms | <= 300 |
| `self_interruption_count` | 0 per session | <= 0 |
| `student_heard_rate` | 100% | >= 1.0 |
| `mid_word_cutoff_count` | 0 | <= 0 |

---

### Step 3 — PoC 02: Proactive Vision

**Why third:** #1 demo moment. Needs clean audio pipeline (Step 2) to know when student is truly silent.

#### What to extract

| Function | What it does |
|---|---|
| `_idle_orchestrator()` | Async loop: monitors silence + camera, sends poke then nudge |
| `_check_proactive_trigger()` | Evaluates if tutor should speak based on camera + silence duration |
| `_send_hidden_turn()` | Sends invisible context message via `queue.send_content()` |
| `_sanitize_tutor_output()` | Strips internal control text from responses |
| `_is_mid_session_restart_text()` | Prevents re-greeting mid-session |
| Config constants | `ORGANIC_POKE_THRESHOLD_S`, `HARD_NUDGE_THRESHOLD_S`, etc. |

#### State dependencies (wire to session state)
- `assistant_speaking`, `client_speech_active`, `camera_active`
- `last_student_activity_at`, `last_tutor_audio_at`, `turn_count`

#### ADK adaptation
- Hidden turns use `queue.send_content()` instead of raw `session.send_realtime_input(text=...)`
- Orchestrator reads state from ADK session (`tool_context.state` keys or shared dict)

#### Metrics (from PoC 02 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `proactive_trigger_rate` | 100% when camera + silent 15s | >= 1.0 |
| `false_positive_rate` | 0% (no camera = no trigger) | <= 0.0 |
| `disclosure_size` | 1 concept per proactive turn | <= 1 |
| `audio_overlap_count` | 0 | <= 0 |

---

### Step 4 — PoC 10: Screen Share Toggle

**Why fourth:** Extends the visual pipeline from Step 3. Both camera and screen frames feed into the same Gemini video input. Proactive vision must work on both sources.

#### What to extract

| Function / Logic | What it does |
|---|---|
| `source_switch` handler | Receives toggle message, injects hidden turn ("I can see your screen now") |
| `stop_sharing` handler | Reverts to camera, injects "Visual input stopped" turn |
| `_is_visual_active()` | Updated check: camera OR screen is active |
| `screen_frame` message type | Same as `camera_frame` — forwarded as video blob |
| Switch metrics | `source_switches`, `switch_to_screen_count`, `switch_to_camera_count` |

#### ADK adaptation
- `source_switch` / `stop_sharing` messages handled in upstream task, inject hidden turns via `queue.send_content()`
- Screen frames use same `queue.send_realtime(video_blob)` as camera frames

#### Metrics (from PoC 10 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `switch_latency_ms` | < 500ms | <= 500 |
| `switches_without_reconnect` | 5+ in a row with 0 reconnects | >= 5 |
| `audio_continuity` | 0 dropped audio chunks during switch | <= 0 |
| `tutor_acknowledgement_rate` | 100% of switches acknowledged | >= 1.0 |
| `permission_denied_recovery` | 100% | >= 1.0 |

---

### Step 5 — PoC 04: Whiteboard Sync

**Why fifth:** First tool output. Needs stable turns (Step 2) and session lifecycle (Step 1) to sync note delivery with speech.

#### What to extract

| Function | What it does |
|---|---|
| `_normalize_note_type()` | Validates note type |
| `_normalize_title()` / `_normalize_content()` | Cleans and formats |
| `_dedupe_key()` | Hash-based deduplication |
| `_whiteboard_dispatcher()` | Async loop: queues notes, dispatches synced with speech |

#### ADK adaptation
- `write_notes` already a FunctionTool with ToolContext (Step 0)
- ADK dispatches tool calls automatically — no manual `_dispatch_tool_call` needed
- Whiteboard dispatcher remains as async task (reads from queue populated by tool)

#### Metrics (from PoC 04 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `whiteboard_usage_count` | >= 1 note per session | >= 1 |
| `note_delivery_latency_ms` | < 500ms | <= 500 |
| `audio_glitch_during_note` | 0 | <= 0 |
| `duplicate_notes` | 0 | <= 0 |

---

### Step 6 — PoC 03: Multilingual

**Why sixth:** Clean module — mostly pure functions. Plugs in without disrupting existing flow.

#### What to extract → `backend/language.py`

| Functions | What they do |
|---|---|
| `_build_language_contract()` | Generates system prompt language contract |
| `_detect_language()` / `_analyze_turn_language()` | Language detection |
| `_is_confusion_signal()` | Detects confusion patterns |
| `_expected_language()` | Returns expected language for current turn |
| `_build_internal_control()` | Builds language correction hidden turn |
| `_handle_student_transcript()` | Hook: analyze student speech |
| `_finalize_tutor_turn()` | Hook: analyze tutor output |

#### ADK adaptation
- Language contract injected into Agent's `instruction` at session start
- Hooks called from downstream event processing (transcript events → `_handle_student_transcript`)
- Language corrections sent via `queue.send_content()`

#### Metrics (from PoC 03 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `language_purity_rate` | > 98% | >= 0.98 |
| `guided_bilingual_adherence` | > 95% | >= 0.95 |
| `fallback_trigger_turns` | < 1 turn after confusion | <= 1 |
| `mixed_language_turns` | 0 | <= 0 |

---

### Step 7 — PoC 05: Search Grounding

**Why seventh:** Quick win. One function + config change. Visible hallucination avoidance — explicitly scored by judges.

#### What to extract

| Item | What it does |
|---|---|
| `_extract_grounding(event)` | Parses ADK events for grounding metadata |
| System prompt addition | Grounding rules: when to search, when not to |
| WebSocket message | `{ type: "grounding", data: { snippet, source, url } }` |

#### ADK adaptation
- `google_search` already added as tool in Step 0
- Grounding metadata extracted from ADK events in downstream task

#### Metrics (from PoC 05 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `grounding_on_facts_count` | >= 1 per factual question | >= 1 |
| `grounding_on_coaching_count` | 0 on coaching turns | <= 0 |
| `citation_render_rate` | 100% | >= 1.0 |
| `search_stall_count` | 0 | <= 0 |

---

### Step 8 — PoC 09: Safety & Scope Guardrails

**Why eighth:** Protective layer on top of everything. Must catch issues across ALL capabilities.

#### What to extract → `backend/guardrails.py`

| Function | What it does |
|---|---|
| `_check_student_input_guardrails(text)` | Off-topic, cheat requests, inappropriate content |
| `_check_tutor_output_guardrails(text)` | Answer leak detection |
| `_sanitize_tutor_output(text)` | Strips internal control text |
| `_record_guardrail_event()` | Logs triggers |
| Reinforcement prompts | SOCRATIC_REINFORCE, SCOPE_REINFORCE, CAMERA_UNCLEAR_REINFORCE |

#### ADK adaptation
- Input guardrails: ADK callback `before_model_call` — check student input, block if needed
- Output guardrails: ADK callback `after_model_call` — check tutor output, inject reinforcement
- This is the ADK way: callbacks for responsible AI (section 5 of ADK docs)

#### Metrics (from PoC 09 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `socratic_compliance_rate` | >= 90% | >= 0.90 |
| `off_topic_refusal_rate` | 100% | >= 1.0 |
| `cheat_refusal_rate` | 100% | >= 1.0 |
| `camera_unclear_protocol_rate` | 100% | >= 1.0 |
| `hallucination_count` | 0 on unknown facts | <= 0 |

---

### Step 9 — PoC 07: Latency Instrumentation

**Why last:** Measuring a broken system is pointless. Now everything works — prove it's fast.

#### What to extract → `backend/latency.py`

| Item | What it does |
|---|---|
| `class LatencyStats` | Self-contained stat tracker: record(), avg, p95, min, max |
| `_send_latency_event()` | Pushes individual measurements to frontend |
| `_send_latency_report()` | Pushes aggregated report on turn_complete |
| Timestamp hooks | `time.time()` at key pipeline points |

#### ADK adaptation
- Timestamps collected in upstream/downstream tasks
- `LatencyStats` is pure Python — no ADK changes needed

#### Metrics (from PoC 07 prd.md)

| Metric | Target | Threshold |
|---|---|---|
| `response_start_latency_ms_avg` | < 500ms | <= 500 |
| `interruption_stop_latency_ms_avg` | < 200ms | <= 200 |
| `summary_export` | Clean JSON | true |

---

## Phase 2 — Frontend (single unified build)

After all backend steps pass, build one clean `index.html`:

1. **Core audio/video pipe** — mic capture (16kHz PCM), camera capture (1 FPS JPEG), audio playback (24kHz)
2. **Silero VAD + audio gate** — from PoC 01 frontend
3. **Barge-in confirmation** — 220ms window + loudness check
4. **Screen share toggle** — getDisplayMedia, toggle button, LIVE badge, stop sharing
5. **Whiteboard UI** — note cards with slide-in animation, status badges, auto-scroll
6. **Citation toasts** — bottom-right overlay for search grounding
7. **Connection state banner** — reconnecting/reconnected/ended
8. **Latency HUD** — toggleable overlay with color-coded budget indicators
9. **PWA polish** — app icon, splash screen, mobile-friendly, "Add to Home Screen"

**Frontend sections marked with:** `// === CAPABILITY: NAME ===` comments

---

## Integration Order Summary

### Phase 0 — Isolate (in PoC directories)

| # | PoC | Module | Difficulty | Est. time |
|---|---|---|---|---|
| P0.1 | 05 Search Grounding | `grounding.py` | Trivial | 15 min |
| P0.2 | 07 Latency | `latency.py` | Low | 20 min |
| P0.3 | 03 Multilingual | `language.py` | Low | 30 min |
| P0.4 | 09 Safety Guardrails | `guardrails.py` | Low-Med | 30 min |
| P0.5 | 04 Whiteboard | `whiteboard.py` | Medium | 45 min |
| P0.6 | 02 Proactive Vision | `proactive.py` | Medium | 45 min |
| P0.7 | 10 Screen Share | `screen_share.py` | Medium | 30 min |
| P0.8 | 06 Session Resilience | `session_state.py` | High | 60 min |
| P0.9 | 01 Interruption | (config only) | Trivial | 10 min |

### Phase 1 — Backend Integration (main `backend/`)

| Step | PoC | Why this order | Core lines | Difficulty |
|---|---|---|---|---|
| 0 | ADK Skeleton | Foundation — replaces raw API with ADK | ~400 | High |
| 1 | 06 Session Resilience | Changes lifecycle — do first | ~400 | High |
| 2 | 01 Interruption | Audio config — everything depends on it | ~50 (BE only) | Low |
| 3 | 02 Proactive Vision | #1 demo moment, needs clean audio | ~350 | Medium |
| 4 | 10 Screen Share | Extends visual pipeline | ~150 | Medium |
| 5 | 04 Whiteboard | First tool output, needs stable turns | ~300 | Medium |
| 6 | 03 Multilingual | Clean module, plugs in anywhere | ~500 | Low |
| 7 | 05 Search Grounding | 1 function, quick win | ~100 | Low |
| 8 | 09 Safety Guardrails | Protective layer + ADK callbacks | ~250 | Low-Med |
| 9 | 07 Latency | Measure last, not first | ~200 | Low |

### Phase 2 — Frontend (single unified build)

| Step | What | Difficulty |
|---|---|---|
| 10 | Unified `index.html` — all backend capabilities wired | Medium |

**Total: Phase 0 ~4.5h | Phase 1 ~2,700 lines | Phase 2 ~2,000 lines | Grand total: ~4,700 lines**

---

## Rules While Integrating

1. **One step at a time.** Don't start the next until the current one passes its metrics gate.
2. **Metrics are mandatory.** Every step has a `MetricsCollector` that logs pass/fail at session end.
3. **Compare with PoC baseline.** Run the same test scenario on the PoC, then on the main app. Numbers must match or beat.
4. **Use PoC frontends as test harnesses** during Phase 1. Don't build the final frontend until Phase 2.
5. **If something breaks a previous step, fix it before moving on.** Regressions compound.
6. **Skip debug tools.** No dashboards, no metric overlays, no test panels — just core logic.
7. **System prompt in one place.** Build it up incrementally in `Agent(instruction=...)`.
8. **ADK patterns everywhere.** Use `ToolContext` for state, callbacks for guardrails, `Runner` for session management.
9. **Commit after each passing step.** You want rollback points.
10. **Never mention Claude/Anthropic.** Grep before every commit. Zero tolerance.

---

## Backend Module Structure (target)

```
backend/
├── main.py              # FastAPI + WebSocket + upstream/downstream tasks
├── agent.py             # ADK Agent definition + tool functions + system prompt
├── session_state.py     # SessionState class + MetricsCollector
├── language.py          # Multilingual pure functions (from PoC 03)
├── guardrails.py        # Input/output checks + reinforcement prompts (from PoC 09)
├── latency.py           # LatencyStats class (from PoC 07)
├── whiteboard.py        # Note normalization + dispatcher (from PoC 04)
├── requirements.txt
└── Dockerfile
```

---

## Security Hardening

> **When to do it:** Alongside Step 0 (ADK skeleton). These are server bootstrap changes, not feature work. All four changes go in `main.py`; one in `deploy.sh`; one in `frontend/index.html`.

### Why it matters for the hackathon

Judges reviewing the code will see `allow_origins=["*"]` + `allow_credentials=True` immediately. This combination is a known anti-pattern (technically invalid per spec — browsers reject credentials with wildcard origins). Missing security headers are equally visible. These are 30-second fixes that signal whether the engineer thinks about production quality. The "Technical Implementation" criterion (30% weight) explicitly scores edge-case handling.

---

### Change 1 — Fix CORS (`backend/main.py`, lines 564–570)

```python
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS",
        "https://seeme-tutor.web.app,http://localhost:8000"
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)
```

`allow_credentials=False` is correct — the app uses no cookies. Origins are configurable via `ALLOWED_ORIGINS` env var.

---

### Change 2 — Security headers middleware (`backend/main.py`, after CORS block)

```python
@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "  # required: single-file PWA uses inline scripts
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self' wss: ws:; "
        "media-src 'self' blob:; "
        "img-src 'self' data:; "
        "frame-ancestors 'none';"
    )
    return response
```

---

### Change 3 — Per-IP WebSocket rate limiting (`backend/main.py`)

No new dependencies — uses stdlib `defaultdict`. Add after constants block:

```python
from collections import defaultdict

_WS_RATE_WINDOW_S = 60.0
_WS_RATE_MAX_PER_WINDOW = 10
_ws_ip_timestamps: dict[str, list[float]] = defaultdict(list)

def _check_ws_rate_limit(client_ip: str) -> bool:
    now = time.time()
    ts = _ws_ip_timestamps[client_ip]
    ts[:] = [t for t in ts if t > now - _WS_RATE_WINDOW_S]
    if len(ts) >= _WS_RATE_MAX_PER_WINDOW:
        return False
    ts.append(now)
    return True
```

In the WebSocket handler, right after `await websocket.accept()`:

```python
client_ip = websocket.client.host if websocket.client else "unknown"
if not _check_ws_rate_limit(client_ip):
    logger.warning("Rate limit exceeded for IP %s", client_ip)
    await websocket.close(code=1008, reason="Too many connections")
    return
```

---

### Change 4 — Payload size guard (`backend/main.py`, before `base64.b64decode` ~line 1296)

Insert between the `if not encoded_data` check and the `base64.b64decode()` call:

```python
_MAX_AUDIO_FRAME_B64 = 400_000    # ~300 KB binary — well above any single 100ms PCM frame
_MAX_VIDEO_FRAME_B64 = 4_000_000  # ~3 MB binary — generous for a JPEG thumbnail

if isinstance(encoded_data, str):
    max_b64 = _MAX_VIDEO_FRAME_B64 if msg_type == "video" else _MAX_AUDIO_FRAME_B64
    if len(encoded_data) > max_b64:
        logger.warning(
            "Oversized %s frame rejected (len=%d) — session %s",
            msg_type, len(encoded_data), session_id,
        )
        continue
```

---

### Change 5 — Add ALLOWED_ORIGINS to `deploy.sh` (line 126)

```bash
--set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION},ALLOWED_ORIGINS=https://seeme-tutor.web.app"
```

---

### Change 6 — Demo code: `localStorage` → `sessionStorage` (`frontend/index.html`)

The demo access code should not persist across browser sessions. Change 3 occurrences (lines 1717, 1725, 1776):

```javascript
// line 1717
sessionStorage.getItem('demoAccessCode')
// line 1725
sessionStorage.setItem('demoAccessCode', demoCode)
// line 1776
sessionStorage.removeItem('demoAccessCode')
```

---

### Change 7 — Remove commented API key from `.env`

Line 19 of `.env` contains a real commented-out Gemini API key. The file is gitignored so it was never committed, but remove the line for cleanliness.

> **Manual action required:** Rotate the key in GCP Console → APIs & Services → Credentials as a precaution.

---

### Pass gate

```bash
# Security headers present
curl -I http://localhost:8000/ | grep -E "X-Frame|X-Content|Content-Security"

# CORS rejects unknown origins (must NOT return Access-Control-Allow-Origin for evil.com)
curl -v -H "Origin: https://evil.com" http://localhost:8000/api/profiles 2>&1 | grep -i "access-control"

# Smoke test: full mic + camera session still works normally

# Rate limit: 11th rapid WS connection from same IP closes with code 1008
```
