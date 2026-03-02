# To Be Fixed & To Be Tested

## Latest Scorecard (Mar 2, session e37f6d58 — 16:29)

| Stat | Value |
|---|---|
| Checks passed | 21 / 51 (43.8%) |
| Checks failed | 3 |
| Checks not tested | 27 |
| POCs fully passing | 1 (Memory) |
| POCs partial | 6 |
| POCs failing | 2 |
| POCs untested | 7 |

---

## Session Log

| Session | Date | Duration | Student/Tutor turns | Camera | Key observation |
|---|---|---|---|---|---|
| `85c3966f` | Mar 1 19:53 | 6.8 min | 26 / 41 | OFF | Baseline — question ratio 91%, no video |
| `678a4c76` | Mar 1 21:06 | 4.6 min | 17 / 26 | ON (254 frames) | Active convo — proactive correctly skipped |
| `678a4c76` | Mar 1 21:22 | ~2 min | 0 / 1 | ON (screen share) | Silent test — proactive FAILED despite 37s silence |
| `c8a427e6` | Mar 2 11:12 | 1.7 min | 4 / 12 | ON | Proactive poke=1, whiteboard=6, question streak=7 |
| `e37f6d58` | Mar 2 14:00 | 3.25 min | 13 / 13 | OFF | Interruptions=2✅, question ratio 63.6%, mastery protocol active, Portuguese switching works |
| `e37f6d58` | Mar 2 16:29 | 1.1 min | 4 / 8 | OFF | **google_search called 2×** (1 success, 1 error), question ratio 50%/streak 1 ✅, grounding metadata NOT captured (extraction gap) |

---

## Priority Order (judging-criteria-aligned)

Features are ordered by **impact on judging score** (40% UX, 30% Technical, 30% Demo).

| # | Item | Judging Impact | Status |
|---|---|---|---|
| 1 | F19. Question balance (F1) | Demo 30% — interrogation loops kill the experience | ⚠️ IMPROVED (100%→63.6%, streak 7→3) — needs more tuning |
| 2 | F02. Interruption handling (T2) | UX 40% — **category requirement** for Live Agents | ✅ WORKING (2 interruptions detected, P99 checkpoint PASS) |
| 3 | F09. Search grounding (T3) | Tech 30% — rubric says "hallucination avoidance + grounding evidence" | 🔧 PARTIAL — google_search called 2× (1 success), but grounding metadata extraction not capturing citations. |
| 4 | F03. Multilingual (T7) | UX 40% — demo differentiation (3 languages = strong) | ✅ WORKING functionally — tutor switches to PT fluently. ⚠️ Language tracking module NOT measuring (events=[]) |
| 5 | F01. Proactive vision (T1) | UX 40% — "beyond text" differentiator | ⬜ NOT TESTED this session (camera OFF). Previously: poke=1 in c8a427e6 |
| 6 | F07. Mastery verification | UX 40% — shows depth of tutoring intelligence | ⚠️ PARTIALLY WORKING — protocol fires (4 tool calls), premature mastery blocked, but 0 full verifications completed |
| 7 | F06. Whiteboard latency (P04) | Tech 30% — p95 518.5ms vs target 500ms | ⚠️ MARGINAL — 518.5ms p95, 18.5ms over target |
| 8 | F05. Screen share toggle (T4) | UX 40% — media interleaving | ⬜ NOT TESTED |
| 9 | F08. Idle/away flow (T5) | UX 40% — context-awareness | ⚠️ PARTIAL — checkin_1 fires (1), away mode not triggered (session too short) |
| 10 | F13. Latency budget (P07) | Tech 30% — all null, not instrumented | ❌ NOT INSTRUMENTED — latency.events=[], reports=[] despite 13 turns |
| 11 | F11. Session resilience (T6) | Tech 30% — error/edge case handling | ⬜ NOT TESTED |
| 12 | F04. Emotional adaptation | UX 40% — qualitative only | ⚠️ QUALITATIVE EVIDENCE — tutor said "Sem problemas! É normal ter dúvidas" when student expressed doubt |

---

## To Be Fixed

### F1. Question-ending ratio — MAJOR REWRITE: coach, don't interrogate
- **Severity:** MEDIUM (was HIGH) — leading questions feel fake, users hate them
- **Judging criteria:** Demo & Presentation (30%) — "natural immersive interaction"
- **History:** 85c3: 91.3%/streak 16 → c8a4: 100%/streak 7 → e37f: 63.6%/streak 3
- **Root cause:** Prompt philosophy was "ask leading questions" — now flipped to "suggest what to try"
- **Fix:** Complete rewrite of Core Teaching Philosophy + Turn Variety. Tutor now coaches (suggestions, hints, encouragement) instead of interrogating. Questions only for genuine unknowns or brief check-ins.
- **Metric:** `prd_scorecard.pocs.poc_02.P02.question_turn_ratio` + `P02.question_streak_max`
- **Target:** ratio 15-25%, streak <= 1
- **Current:** ratio 63.6%, streak 3 (pre-rewrite)
- **Status:** PROMPT REWRITTEN — needs test validation

### F2. Search grounding — ROOT CAUSE FOUND & FIXED, needs test verification
- **Severity:** HIGH — rubric explicitly scores "hallucination avoidance and grounding evidence"
- **Judging criteria:** Technical Implementation (30%)
- **Progress (session e37f6d58 @ 16:29):**
  - ✅ **google_search IS now being called by the model** — 2 calls in latest session:
    1. `google_search("latest price of telc A2 exam in Berlin")` → **success** (8,562ms)
    2. `google_search("German possessive pronouns sein and ihr rules")` → **error** (2,734ms)
  - ❌ **Grounding metadata not captured:** `grounding.events = 0`, `citations_sent = 0`, `search_queries = []`
- **Root cause analysis (completed Mar 2):**
  Two grounding metadata propagation mechanisms exist in ADK v1.25.1:
  1. **Mechanism A (state_delta path):** `GoogleSearchAgentTool.run_async()` captures `event.grounding_metadata` from sub-agent events → stores in `tool_context.state['temp:_adk_grounding_metadata']` → flows via `state_delta` to function response event → `extract_grounding_citations()` checks Path 2. **Chain looks correct in code analysis — needs runtime verification.**
  2. **Mechanism B (`_maybe_add_grounding_metadata`):** ADK's `base_llm_flow.py:1022` copies grounding from session state to parent text response events — BUT only if `tool.name == 'google_search_agent'`. **Our tool was renamed to `'google_search'` via `model_copy(update={"name": "google_search"})` in agent.py:2030. This name mismatch meant Mechanism B ALWAYS returned early without doing anything. CONFIRMED BROKEN.**
- **Fix applied (Mar 2):**
  - Removed the `"name": "google_search"` override from `agent.py` — tool now keeps its ADK default name `'google_search_agent'`, matching the check in `base_llm_flow.py:1022`
  - Updated all system prompt references from `google_search` to `google_search_agent`
  - Updated `TOOL_LATENCY_BUDGETS`, `search_topic_context` return message, and report recording to use `google_search_agent`
  - Added diagnostic debug logging to both `grounding.py` (Path 1/Path 2 detection) and `LoggedGoogleSearchAgentTool` (checks if sub-agent captured metadata)
- **Current code:**
  - `agent.py` uses `LoggedGoogleSearchAgentTool(agent=create_google_search_agent(SEARCH_MODEL).model_copy(update={"instruction": SEARCH_AGENT_INSTRUCTION}))` — name defaults to `'google_search_agent'`
  - `modules/grounding.py` — dual-path extraction with verbose debug logging
  - `ws_bridge.py` — grounding check wired into event loop
- **Diagnostic logging added (will show in next test session):**
  - `GOOGLE_SEARCH_GROUNDING` / `GOOGLE_SEARCH_NO_GROUNDING` — whether sub-agent captures metadata
  - `GROUNDING_PATH1` / `GROUNDING_PATH2` / `GROUNDING_PATH2_MISS` — which extraction path fires
  - `GROUNDING_EMPTY` — metadata found but no parseable citations
- **Metric:** `prd_scorecard.pocs.poc_05.P05.grounding_event_count`
- **Target:** >= 1 grounding event when factual search requested
- **Current:** 0 (google_search called but metadata not captured — fix applied, needs test)
- **Status:** 🔧 FIX APPLIED — Mechanism B name mismatch fixed + debug logging added. **Ready for test verification.**

### F3. Language tracking module not recording — measurement gap
- **Severity:** MEDIUM — feature works, can't prove it with metrics
- **Judging criteria:** UX 40% — demo differentiation
- **Symptom:** Tutor fluently switches to Portuguese (confirmed in transcript), but `language.events = []`, `language.latest_metric = {}`
- **Root cause:** Language detection module not firing or not connected to transcript pipeline
- **Metric:** `prd_scorecard.pocs.poc_03.P03.language_purity_rate`
- **Target:** >= 98%
- **Current:** null (not measured, but functional observation: works great)
- **Status:** Needs instrumentation fix — language module needs to process transcripts

### F4. Whiteboard delivery latency marginally over target
- **Severity:** LOW — 518.5ms vs 500ms target, within noise
- **Judging criteria:** Technical Implementation (30%)
- **Metric:** `prd_scorecard.pocs.poc_04.P04.note_delivery_latency_p95`
- **Target:** <= 500ms
- **Current:** 518.5ms (second note was 25.3ms — first note warmup drives p95)
- **Status:** Monitor — likely passes on warmed-up sessions

### F5. Response start latency — all null, not instrumented
- **Severity:** MEDIUM — can't demonstrate responsiveness metrics
- **Judging criteria:** Technical Implementation (30%) — responsiveness
- **Symptom:** `latency.events = []`, `latency.reports = []` despite 13 turns in e37f session
- **Root cause:** Latency recording module not connected or not triggering
- **Metric:** `prd_scorecard.pocs.poc_07.P07.response_start_avg` / `.p95`
- **Target:** avg <= 500ms, p95 <= 800ms
- **Current:** null (raw audio latency samples exist: avg ~35ms, but structured latency module silent)
- **Status:** CODE FIX NEEDED — latency module needs investigation

### F6. Mastery verification never fully completes
- **Severity:** MEDIUM — protocol is active but never finishes all 3 steps
- **Judging criteria:** UX 40% — depth of tutoring intelligence
- **Symptom:** 4 verify_mastery_step calls in e37f: solve✅ → explain✅ → transfer❌ → transfer(wrong_step). Tutor tried to mark "mastered" → blocked by premature_mastery_blocked.
- **Root cause:** Student failed transfer step, then tutor tried to skip ahead (wrong_step error). Protocol enforcement works, but tutor doesn't retry correctly.
- **Metric:** `mastery.verifications_completed`
- **Target:** >= 1
- **Current:** 0 (protocol active, never completed)
- **Status:** System prompt may need guidance on retry flow after transfer failure

### F7. Tutor gave incorrect grammar correction
- **Severity:** MEDIUM — factual accuracy in tutoring
- **Judging criteria:** Technical Implementation (30%) — "hallucination avoidance"
- **Symptom:** Tutor said `seine Buch` (wrong) instead of `sein Buch` (correct neuter nominative)
- **Fix:** Model hallucination issue. Grounding search may help.
- **Status:** Model-level, harder to fix

### F8. TURN_DROPPED flood — wasted compute
- **Severity:** LOW — no user-facing bug
- **Symptom:** ~12+ TURN_DROPPED pairs in e37f session (confirmed in debug.log)
- **Fix:** Consider `send_activity_end()` when turn is dropped
- **Status:** Low priority, investigate if it contributes to latency

---

## To Be Tested

### T1. Search grounding / citations — CRITICAL for Technical score (F2 root cause fixed, READY TO TEST)
- **Judging criteria:** Tech 30% — rubric literally says "hallucination avoidance and grounding evidence"
- **What:** Ask a factual question that triggers Google Search
- **How:** Say "search for the dative case rules in German" or "look up atomic structure"
- **Metric:** `prd_scorecard.pocs.poc_05.P05.grounding_event_count` >= 1, `P05.citation_render_rate` = 100%
- **Current:** 0 grounding events across all sessions (pre-fix). F2 root cause fix applied (name mismatch).
- **Pass criteria:** `grounding.events >= 1`, `grounding.citations_sent >= 1`
- **Blocker:** ~~F2 tool calling~~ ✅ Fixed. ~~Grounding metadata extraction~~ 🔧 Fix applied (tool name mismatch). **Check debug logs for `GOOGLE_SEARCH_GROUNDING` / `GROUNDING_PATH1` / `GROUNDING_PATH2`.**
- **If test still fails:** Check logs for `GOOGLE_SEARCH_NO_GROUNDING` — means sub-agent's Gemini API response doesn't include `grounding_metadata` (model-level issue, may need different SEARCH_MODEL)

### T2. Proactive vision — needs camera-on test
- **Judging criteria:** UX 40% — "visual precision" + "context-awareness"
- **What:** Point camera at homework, stay silent, verify proactive poke fires
- **How:** Camera ON, silence for 15+ seconds
- **Metric:** `proactive.poke_count` >= 1
- **Current:** 0 in e37f (camera OFF). 1 in c8a427e6 (camera ON). Debug log confirms `cam=False` throughout e37f.
- **Pass criteria:** `proactive.poke_count >= 1` with camera active
- **Status:** Previously worked (poke=1), just needs re-verification with camera ON

### T3. Multilingual purity measurement — needs language module fix (blocked by F3)
- **Judging criteria:** UX 40% — demo differentiation (3 languages in one family)
- **What:** Run a full session in one non-English language, measure purity rate
- **How:** Run Portuguese-only session for 3+ min
- **Metric:** `prd_scorecard.pocs.poc_03.P03.language_purity_rate` >= 98%
- **Current:** null (language module not recording). Functional evidence: tutor switches to PT perfectly (see e37f transcript).
- **Pass criteria:** `language_purity_rate >= 98%`
- **Blocker:** F3 (language tracking module not recording) must be fixed first

### T4. Mastery verification completion — needs retry flow (blocked by F6)
- **Judging criteria:** UX 40% — depth of tutoring intelligence, "beyond text"
- **What:** Complete all 3 mastery steps (solve → explain → transfer) on one exercise
- **How:** Answer correctly at each step, stay patient through protocol
- **Metric:** `prd_scorecard.pocs.poc_14.P14.mastery_verifications` >= 1
- **Current:** 0 completed. Protocol IS active (4 tool calls in e37f, premature mastery blocked). Student failed transfer, tutor couldn't retry cleanly.
- **Pass criteria:** `mastery.verifications_completed >= 1`
- **Blocker:** F6 (retry flow after transfer failure) should be addressed

### T5. Screen share toggle
- **Judging criteria:** UX 40% — "media interleaving"
- **What:** Switch between camera and screen share during a session
- **How:** Start with camera, switch to screen share, switch back, then stop sharing
- **Metric:** `prd_scorecard.pocs.poc_10.P10.source_switch_count` >= 1, errors = 0
- **Current:** 0 source switches across all sessions
- **Pass criteria:** `screen_share.source_switches >= 1`, `screen_share.stop_sharing_count >= 1`

### T6. Idle / away flow
- **Judging criteria:** UX 40% — "context-awareness", experience fluidity
- **What:** Go silent long enough to trigger away mode, then resume
- **How:** Stop talking for 2+ min, verify away_activated fires. Speak again, verify resumed.
- **Metric:** `prd_scorecard.pocs.poc_11.P11.away_resume_flow_observed`
- **Current:** checkin_1=1 (fires correctly). away_activated=0 (sessions too short).
- **Pass criteria:** `idle.away_activated_count >= 1`, `idle.away_resumed_count >= 1`

### T7. Session resilience (reconnect)
- **Judging criteria:** Tech 30% — "error/edge case handling"
- **What:** Disconnect and reconnect, verify stream recovers
- **How:** Kill WS mid-session, verify backend retries and reconnects
- **Metric:** `prd_scorecard.pocs.poc_06.P06.reconnect_success_rate` = 100%
- **Current:** 0 retry attempts (no disconnects tested)
- **Pass criteria:** `resilience.stream_reconnect_successes >= 1`

### T8. Latency instrumentation (blocked by F5)
- **Judging criteria:** Tech 30% — responsiveness
- **What:** Run a session long enough for latency reports to populate
- **How:** Have 10+ tutor turns, check latency report
- **Metric:** All POC 07 checks: `response_start.avg <= 500ms`, `.p95 <= 800ms`, `interruption_stop.p95 <= 400ms`
- **Current:** All null despite 13 turns in e37f. Raw audio latency samples exist (avg ~35ms) but structured module is silent.
- **Pass criteria:** All P07 checks populated and within targets
- **Blocker:** F5 (latency module not recording) must be fixed first

### T9. Emotional adaptation (qualitative)
- **Judging criteria:** UX 40% — "natural immersive interaction"
- **What:** Show frustration signals and observe tutor response
- **How:** Say "I don't get it" 3+ times, sigh, show confusion
- **Current:** Qualitative evidence in e37f: tutor said "Sem problemas! É normal ter dúvidas" when student expressed doubt. Promising but needs structured test.
- **Pass criteria:** Qualitative — tutor slows down, simplifies, encourages

### T10. Question balance final validation (validates F1)
- **Judging criteria:** Demo 30% — "experience fluidity"
- **What:** After coach-style prompt rewrite, verify question ratio dropped dramatically
- **How:** Run 5+ min tutoring session, check scorecard
- **Metric:** `P02.question_turn_ratio` 15-25%, `P02.question_streak_max` <= 1
- **Current:** 63.6% ratio, streak 3 (pre-rewrite). Prompt now says "suggest, don't ask."
- **Pass criteria:** ratio 15-25%, streak <= 1
- **Listen for:** Tutor should say "try this", "notice that", "go ahead and..." — not "what do you think?"

---

## Scorecard Target for Submission

### Must Pass (demo-critical)
| Check | Target | Current | Gap |
|---|---|---|---|
| P01.interruptions_observed | >= 1 | **2** ✅ | OK |
| P02.proactive_trigger_count | >= 1 | 0 (cam OFF) | RE-TEST with camera |
| P02.question_turn_ratio | 15-25% | **63.6%** | PROMPT REWRITTEN — test needed |
| P02.question_streak_max | <= 1 | **3** | PROMPT REWRITTEN — test needed |
| P04.whiteboard_usage | >= 1 | **2** ✅ | OK |
| P05.grounding_event_count | >= 1 | 0 (google_search called but metadata not captured) | FIX APPLIED — test needed |
| P09.answer_leaks | 0 | **0** ✅ | OK |
| P99.interruption_checkpoint | pass | **pass** ✅ | OK |
| P99.grounding_checkpoint | pass | fail (tool works, metadata gap) | FIX APPLIED — test needed |

### Should Pass (competitive edge)
| Check | Target | Current | Gap |
|---|---|---|---|
| P03.language_purity_rate | >= 98% | null (works, not measured) | FIX F3 |
| P07.response_start_avg | <= 500ms | null | FIX F5 |
| P07.response_start_p95 | <= 800ms | null | FIX F5 |
| P14.mastery_verifications | >= 1 | 0 (protocol active) | FIX F6 + TEST |
| P11.away_resume_flow | activated+resumed | checkin=1 only | TEST (longer session) |
| P10.source_switch_count | >= 1 | null | TEST |

### Nice to Have (bonus differentiation)
| Check | Target | Current | Gap |
|---|---|---|---|
| P06.reconnect_success_rate | 100% | null | TEST |
| P06.session_resumption | 100% | null | TEST |
| P13.memory_recall | >= 1 | **1** ✅ | OK |
| P13.checkpoints_saved | >= 1 | **2** ✅ | OK |

---

## Overall Goal: 20/51 → 35+/51 checks passing before submission

Current auto pass rate: **41.7%**. Target: **70%+**.

### What's working (confirmed in e37f6d58)
- ✅ Interruption handling (2 detected, stale filtering works)
- ✅ Whiteboard sync (2 notes created + delivered)
- ✅ Memory management (all 5 checks pass)
- ✅ Safety guardrails (0 answer leaks, Socratic 100%)
- ✅ Multilingual switching (functionally — tutor speaks PT fluently)
- ✅ Mastery protocol fires (4 tool calls, premature mastery blocked)
- ✅ Idle checkin fires (1 gentle check)
- ⚠️ Question balance improved (100% → 63.6%, streak 7 → 3)

### Critical path to 70%+
1. **FIX F2 (search grounding)** — ✅ google_search now called by model (2 calls in e37f@16:29). 🔧 Root cause found: tool name mismatch (`'google_search'` vs expected `'google_search_agent'` in ADK `base_llm_flow.py:1022`). **Fix applied** — removed name override, updated all references. **READY FOR TEST.** (unblocks T1, P99.grounding, P05 = 3 checks)
2. **TUNE F1 (question ratio)** — one more prompt iteration to hit 35-50% (2 checks)
3. **FIX F3 (language module)** — unblocks P03 measurement (1 check)
4. **FIX F5 (latency module)** — unblocks P07 (5 checks)
5. **TEST with camera ON** — validates proactive poke (1 check)
6. **FIX F6 (mastery retry)** — unblocks P14.mastery_verifications (1 check)

**Fixing F2 + F5 + one prompt tune + camera test = ~32 checks passing (62%). Add F3 + F6 = ~35+ (70%+).**
