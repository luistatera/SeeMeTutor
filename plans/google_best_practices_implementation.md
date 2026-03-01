# Winning Implementation Plan: Google Best Practices + Memory Management

## Objective

Ship a competition-ready version of SeeMe Tutor that is robust in long live sessions, survives disconnects, and preserves learning continuity across sessions.

Memory management is non-negotiable and is treated as a P0 deliverable.

## Why This Plan Wins

1. Prevents live demo failures from context overflow or stream instability.
2. Demonstrates production architecture (resumption + bounded context + observability).
3. Proves educational continuity with structured memory recall, not just reconnect UX.
4. Improves scorecard performance toward:
   - `auto_pass_rate_percent >= 85`
   - `poc_99_hero_flow_rehearsal.checklist_completed == 6`

---

## P0 Scope (Must Ship)

1. Native context window compression in the live runtime path.
2. True session resumption (token/handle based), not only reconnect retries.
3. Memory management pipeline (checkpoint summaries + typed long-horizon recall).
4. Judge-visible diagnostics for compression/resumption/memory.

---

## Phase 0: SDK Capability Spike (Blocker Removal, 1-2h)

Goal: confirm exact ADK/GenAI fields available in installed versions before coding.

Tasks:

1. Validate supported config fields in the active runtime:
   - `RunConfig`
   - Live API compression config type/field names
   - Session resumption handle field names and event location
2. Record findings in a short compatibility note under `docs/`.
3. Define fallback path if a field is unavailable in current SDK:
   - keep deterministic checkpoint summarization and memory recall as safety net
   - keep retry/backoff as transport fallback

Deliverable:

- Compatibility matrix with "supported / unsupported / fallback" for compression and resumption.

---

## Phase 1: Context Window Compression (P0)

Current gap: no explicit compression config in the live run path.

Implementation:

1. Configure compression where live calls are executed in `backend/main.py` (the `run_live(..., run_config=ADK_RUN_CONFIG)` path), not only on `Agent(...)`.
2. Add env-driven thresholds:
   - `LIVE_COMPRESSION_TRIGGER_TOKENS=32000`
   - `LIVE_COMPRESSION_TARGET_TOKENS=16000`
3. Add runtime telemetry counters:
   - compression events
   - last compression timestamp
   - estimated pre/post token footprint (if available from API; otherwise estimated)
4. Emit frontend diagnostic event when compression triggers:
   - `{ "type": "assistant_state", "data": { "state": "compressing_context", ... } }`

Files:

- `backend/main.py`
- `backend/modules/latency.py` (if reusing metric event structure)
- `frontend/index.html` (diagnostic rendering)

Acceptance Criteria:

1. Backend starts with compression enabled and no runtime errors.
2. 20+ minute audio+video session does not fail from context overflow in normal test conditions.
3. Compression events appear in logs and diagnostics.

---

## Phase 2: True Session Resumption (P0)

Current gap: reconnect exists, but no explicit session resumption handle flow.

Implementation:

1. Capture resumption handle/token from live API events and keep it in session runtime state.
2. Persist handle with metadata in Firestore:
   - `student_id`, `session_id`, `handle`, `created_at`, `expires_at`
3. On client reconnect:
   - frontend sends resume intent/handle (or backend resolves latest valid handle for student)
   - backend attempts resumed live session first, then falls back to fresh session if invalid/expired
4. Keep robust backoff:
   - increase backend stream retries to 3 with exponential backoff + jitter
5. Add explicit user-visible states:
   - `reconnecting`
   - `resumed`
   - `resume_failed_fallback_fresh`

Files:

- `backend/main.py`
- `frontend/index.html`
- Firestore schema additions in `sessions` or a `session_resumption` collection

Acceptance Criteria:

1. Short network drop resumes same conversational continuity without fresh greeting.
2. Resumption success/failure is measurable in session report metrics.
3. `poc_06_session_resilience` moves to pass in measured runs.

---

## Phase 3: Memory Management (P0, Non-Negotiable)

This phase includes both compression-safe checkpoints and long-horizon recall.

### 3A. Pedagogical Checkpoint Summaries (in-session)

Implementation:

1. Every 5 minutes and on topic transition, generate structured checkpoint summary:
   - active topic/subtopic
   - what was mastered
   - current struggle points
   - next recommended step
2. Persist checkpoint snapshots to Firestore under session scope.
3. Update `resume_message` and hidden context using latest checkpoint summary.

### 3B. Typed Long-Horizon Memory Cells (cross-session)

Implementation:

1. Introduce typed memory schema:
   - `fact`, `plan`, `preference`, `decision`, `task`, `risk`
2. Add salience and trace metadata:
   - `salience`, `topic_id`, `source_session_id`, timestamps
3. On new session start / topic switch:
   - retrieve top-k relevant memory cells + compact scene summary
   - inject as hidden context block before normal tutoring starts
4. Add budget guardrail:
   - hard cap for injected memory payload
   - degrade gracefully by selecting top salience only
5. Conflict policy:
   - if recalled memory conflicts with current student input, tutor confirms instead of asserting

Files:

- `backend/modules/memory_store.py` (new)
- `backend/modules/memory_manager.py` (new)
- `backend/main.py` (ingestion + retrieval hooks)
- `backend/agent.py` (memory usage instruction updates)

Acceptance Criteria:

1. Memory cells and checkpoint summaries are written at session end and during long sessions.
2. Next session references prior valid context within first 90 seconds.
3. Memory injection always stays under configured budget.
4. `poc_13_memory_management` moves from `not_tested` to measured/pass.

---

## Phase 4: Judge-Visible UX + Diagnostics (P0)

Goal: make resilience and memory architecture visible during demo.

Implementation:

1. Add diagnostics card/status items in frontend:
   - context compression count
   - session resumed (yes/no + timestamp)
   - memory recall active (count + topic labels)
   - latest checkpoint time
2. Stream these backend events:
   - `context_compression`
   - `session_resumed`
   - `memory_checkpoint_saved`
   - `memory_recall_applied`
3. Keep concise, non-technical user copy for demo clarity.

Files:

- `frontend/index.html`
- `backend/main.py`

Acceptance Criteria:

1. A judge can observe resilience/memory features without opening backend logs.
2. Architecture claims are demonstrable live on-screen.

---

## Phase 5: Verification and Go/No-Go

### Automated / Unit

1. Add tests:
   - `backend/tests/test_resumption.py`
   - `backend/tests/test_memory_management.py`
   - update `backend/tests/test_resilience.py`
2. Ensure existing suite still passes.

### Manual (Use `docs/MIGRATION_PENDING_TESTS.md`)

1. Run Step 7 (latency + resilience) with evidence.
2. Run memory continuity scenario:
   - session A creates struggles/goals
   - session B resumes with correct recall
3. Run 20+ minute session with camera enabled.

### Scorecard Gate (Release Blockers)

1. `auto_pass_rate_percent >= 85`
2. `poc_06_session_resilience = pass`
3. `poc_13_memory_management = pass`
4. `poc_99_hero_flow_rehearsal.checklist_completed = 6`
5. No critical regressions in interruption, grounding, or multilingual behavior

---

## File-Level Implementation Map

1. `backend/main.py`
   - live compression config
   - resumption handle capture/use
   - periodic checkpoint triggers
   - memory retrieval/injection hook
   - diagnostic event emission
2. `backend/agent.py`
   - memory-aware instruction additions
   - conflict handling guidance
3. `backend/modules/memory_store.py` (new)
   - Firestore CRUD for memory cells and scene summaries
4. `backend/modules/memory_manager.py` (new)
   - extraction, salience scoring, top-k selection, budget enforcement
5. `frontend/index.html`
   - reconnect/resume signal handling
   - memory/compression diagnostics display
6. `backend/tests/*`
   - resumption + memory coverage

---

## Execution Sequence (Recommended)

1. Phase 0 (capability spike)
2. Phase 1 (compression)
3. Phase 2 (resumption)
4. Phase 3A (checkpoint summaries)
5. Phase 3B (typed long-horizon memory)
6. Phase 4 (judge-visible diagnostics)
7. Phase 5 (verification + scorecard gate)

This order maximizes demo stability first, then adds the memory-based differentiation that improves winning odds.
