# To Be Fixed & To Be Tested

## Latest Scorecard (Mar 2, session c8a427e6)

| Stat | Value |
|---|---|
| Checks passed | 19 / 49 (41.3%) |
| Checks failed | 5 |
| Checks not tested | 25 |
| POCs fully passing | 1 (Memory) |
| POCs partial | 5 |
| POCs failing | 3 |
| POCs untested | 7 |

---

## Session Log

| Session | Date | Duration | Student/Tutor turns | Camera | Key observation |
|---|---|---|---|---|---|
| `85c3966f` | Mar 1 19:53 | 6.8 min | 26 / 41 | OFF | Baseline — question ratio 91%, no video |
| `678a4c76` | Mar 1 21:06 | 4.6 min | 17 / 26 | ON (254 frames) | Active convo — proactive correctly skipped |
| `678a4c76` | Mar 1 21:22 | ~2 min | 0 / 1 | ON (screen share) | Silent test — proactive FAILED despite 37s silence |
| `c8a427e6` | Mar 2 11:12 | 1.7 min | 4 / 12 | ON | Proactive poke=1, whiteboard=6, question streak=7 |

---

## Priority Order (judging-criteria-aligned)

Features are ordered by **impact on judging score** (40% UX, 30% Technical, 30% Demo).

| # | Item | Judging Impact | Status |
|---|---|---|---|
| 1 | F19. Question balance (F1) | Demo 30% — interrogation loops kill the experience | FIX NEEDED |
| 2 | F02. Interruption handling (T2) | UX 40% — **category requirement** for Live Agents | TEST NEEDED |
| 3 | F09. Search grounding (T3) | Tech 30% — rubric says "hallucination avoidance + grounding evidence" | TEST NEEDED |
| 4 | F03. Multilingual (T7) | UX 40% — demo differentiation (3 languages = strong) | TEST NEEDED |
| 5 | F01. Proactive vision (T1) | UX 40% — "beyond text" differentiator | VERIFY (poke=1, needs more) |
| 6 | F07. Mastery verification | UX 40% — shows depth of tutoring intelligence | TEST NEEDED |
| 7 | F06. Whiteboard latency (P04) | Tech 30% — p95 502.7ms vs target 500ms | FIX NEEDED (marginal) |
| 8 | F05. Screen share toggle (T4) | UX 40% — media interleaving | TEST NEEDED |
| 9 | F08. Idle/away flow (T5) | UX 40% — context-awareness | TEST NEEDED |
| 10 | F13. Latency budget (P07) | Tech 30% — all null, not instrumented in latest | TEST NEEDED |
| 11 | F11. Session resilience (T6) | Tech 30% — error/edge case handling | TEST NEEDED |
| 12 | F04. Emotional adaptation | UX 40% — qualitative only | TEST NEEDED |

---

## To Be Fixed

### F1. Question-ending ratio too high — CRITICAL for Demo score
- **Severity:** HIGH — judges hear an interrogation, not a tutor
- **Judging criteria:** Demo & Presentation (30%) — "natural immersive interaction"
- **Symptom:** Session c8a427e6: 100% question ratio, streak of 7. Session 85c3: 91.3%, streak 16.
- **Root cause:** System prompt over-indexes on Socratic questioning without variety
- **Fix:** Tune system prompt: "After 2 consecutive questions, give a statement or encouragement before asking again."
- **Metric:** `prd_scorecard.pocs.poc_02.P02.question_turn_ratio` + `P02.question_streak_max`
- **Target:** ratio 35-50%, streak <= 2
- **Current:** ratio 100%, streak 7
- **Status:** Needs system prompt fix in `agent.py`

### F2. Whiteboard delivery latency marginally over target
- **Severity:** LOW — 502.7ms vs 500ms target, within noise
- **Judging criteria:** Technical Implementation (30%)
- **Metric:** `prd_scorecard.pocs.poc_04.P04.note_delivery_latency_p95`
- **Target:** <= 500ms
- **Current:** 502.7ms
- **Status:** Likely passes on next run, monitor

### F3. Response start latency — all null in latest run
- **Severity:** MEDIUM — dead air while student hears nothing
- **Judging criteria:** Technical Implementation (30%) — responsiveness
- **Symptom:** Session 85c3: avg 787ms (target <=500ms), p95 1,590ms (target <=800ms). Latest session: null (latency module may not be recording)
- **Metric:** `prd_scorecard.pocs.poc_07.P07.response_start_avg` / `.p95`
- **Target:** avg <= 500ms, p95 <= 800ms
- **Current:** null (not instrumented or session too short)
- **Status:** Needs investigation — run longer session to confirm instrumentation

### F4. Tutor gave incorrect grammar correction
- **Severity:** MEDIUM — factual accuracy in tutoring
- **Judging criteria:** Technical Implementation (30%) — "hallucination avoidance"
- **Symptom:** Tutor said `seine Buch` (wrong) instead of `sein Buch` (correct neuter nominative)
- **Fix:** Model hallucination issue. Grounding search may help.
- **Status:** Model-level, harder to fix

### F5. Proactive vision not triggering during silence — PREVIOUSLY CONFIRMED BUG
- **Severity:** HIGH — core "beyond text" feature
- **Judging criteria:** UX 40% — "visual precision" + "context-awareness"
- **Symptom:** Session 678a (21:22): 37s silence with screen share, zero pokes.
- **Root cause:** Speech heuristic resets silence tracking (see detailed trace in previous version)
- **Current:** Partially fixed — c8a427e6 got 1 poke. But needs robust validation with dedicated silent test.
- **Metric:** `prd_scorecard.pocs.poc_02.P02.proactive_trigger_count`
- **Target:** >= 1 poke after 10s silence with camera active
- **Status:** PARTIALLY FIXED — needs re-test with silent-only scenario

### F6. TURN_DROPPED flood — wasted compute
- **Severity:** LOW — no user-facing bug
- **Symptom:** ~12 TURN_DROPPED pairs in session 678a
- **Fix:** Consider `send_activity_end()` when turn is dropped
- **Status:** Low priority, investigate if it contributes to latency

---

## To Be Tested

### T1. Interruption handling — CRITICAL for Live Agents category
- **Judging criteria:** UX 40% — **explicit category requirement** ("interruption handling" in rubric)
- **What:** Actively interrupt tutor mid-speech and verify it stops + acknowledges
- **How:** Start speaking loudly DURING tutor's 1-3s speaking window. Not after.
- **Metric:** `prd_scorecard.pocs.poc_01.P01.interruption_stop_p95` <= 500ms, `P01.interruptions_observed` >= 1
- **Current:** 0 interruptions across all sessions. Tutor speaking windows are short (~2s).
- **Pass criteria:** `interruptions.count >= 1`

### T2. Search grounding / citations — CRITICAL for Technical score
- **Judging criteria:** Tech 30% — rubric literally says "hallucination avoidance and grounding evidence"
- **What:** Ask a factual question that triggers Google Search
- **How:** Say "search for the dative case rules in German" or "look up atomic structure"
- **Metric:** `prd_scorecard.pocs.poc_05.P05.grounding_event_count` >= 1, `P05.citation_render_rate` = 100%
- **Current:** 0 grounding events across all sessions
- **Pass criteria:** `grounding.events >= 1`, `grounding.citations_sent >= 1`

### T3. Multilingual purity — HIGH for UX score
- **Judging criteria:** UX 40% — demo differentiation (3 languages in one family)
- **What:** Run a full session in one non-English language, measure purity rate
- **How:** Run German-only or Portuguese-only session for 3+ min
- **Metric:** `prd_scorecard.pocs.poc_03.P03.language_purity_rate` >= 98%
- **Current:** null (never measured)
- **Pass criteria:** `language_purity_rate >= 98%`

### T4. Mastery verification protocol
- **Judging criteria:** UX 40% — depth of tutoring intelligence, "beyond text"
- **What:** Solve an exercise correctly and verify 3-step mastery protocol fires
- **How:** Answer correctly, check if tutor asks explain-why + transfer problem
- **Metric:** `prd_scorecard.pocs.poc_14.P14.mastery_verifications` >= 1
- **Current:** null (never triggered)
- **Pass criteria:** `mastery.verifications_completed >= 1`

### T5. Screen share toggle
- **Judging criteria:** UX 40% — "media interleaving"
- **What:** Switch between camera and screen share during a session
- **How:** Start with camera, switch to screen share, switch back, then stop sharing
- **Metric:** `prd_scorecard.pocs.poc_10.P10.source_switch_count` >= 1, errors = 0
- **Current:** 0 source switches
- **Pass criteria:** `screen_share.source_switches >= 1`, `screen_share.stop_sharing_count >= 1`

### T6. Idle / away flow
- **Judging criteria:** UX 40% — "context-awareness", experience fluidity
- **What:** Go silent long enough to trigger away mode, then resume
- **How:** Stop talking for 2+ min, verify away_activated fires. Speak again, verify resumed.
- **Metric:** `prd_scorecard.pocs.poc_11.P11.away_resume_flow_observed`
- **Current:** 0 checkins (sessions too short), away never activated
- **Pass criteria:** `idle.away_activated_count >= 1`, `idle.away_resumed_count >= 1`

### T7. Session resilience (reconnect)
- **Judging criteria:** Tech 30% — "error/edge case handling"
- **What:** Disconnect and reconnect, verify stream recovers
- **How:** Kill WS mid-session, verify backend retries and reconnects
- **Metric:** `prd_scorecard.pocs.poc_06.P06.reconnect_success_rate` = 100%
- **Current:** 0 retry attempts (no disconnects tested)
- **Pass criteria:** `resilience.stream_reconnect_successes >= 1`

### T8. Latency instrumentation
- **Judging criteria:** Tech 30% — responsiveness
- **What:** Run a session long enough for latency reports to populate
- **How:** Have 10+ tutor turns, check latency report
- **Metric:** All POC 07 checks: `response_start.avg <= 500ms`, `.p95 <= 800ms`, `interruption_stop.p95 <= 400ms`
- **Current:** All null in latest session
- **Pass criteria:** All P07 checks populated and within targets

### T9. Emotional adaptation (qualitative)
- **Judging criteria:** UX 40% — "natural immersive interaction"
- **What:** Show frustration signals and observe tutor response
- **How:** Say "I don't get it" 3+ times, sigh, show confusion
- **Pass criteria:** Qualitative — tutor slows down, simplifies, encourages

### T10. Question balance after F1 fix (validates F1)
- **Judging criteria:** Demo 30% — "experience fluidity"
- **What:** After fixing system prompt, re-run and verify question ratio dropped
- **How:** Run 5+ min tutoring session, check scorecard
- **Metric:** `P02.question_turn_ratio` 35-50%, `P02.question_streak_max` <= 2
- **Current:** 100% ratio, streak 7
- **Pass criteria:** ratio 35-50%, streak <= 2

---

## Scorecard Target for Submission

### Must Pass (demo-critical)
| Check | Target | Current | Gap |
|---|---|---|---|
| P01.interruptions_observed | >= 1 | 0 | TEST |
| P02.proactive_trigger_count | >= 1 | 1 | OK |
| P02.question_turn_ratio | 35-50% | 100% | FIX |
| P02.question_streak_max | <= 2 | 7 | FIX |
| P04.whiteboard_usage | >= 1 | 6 | OK |
| P05.grounding_event_count | >= 1 | 0 | TEST |
| P09.answer_leaks | 0 | 0 | OK |
| P99.interruption_checkpoint | pass | fail | TEST |
| P99.grounding_checkpoint | pass | fail | TEST |

### Should Pass (competitive edge)
| Check | Target | Current | Gap |
|---|---|---|---|
| P03.language_purity_rate | >= 98% | null | TEST |
| P07.response_start_avg | <= 500ms | null | TEST |
| P07.response_start_p95 | <= 800ms | null | TEST |
| P14.mastery_verifications | >= 1 | null | TEST |
| P11.away_resume_flow | activated+resumed | null | TEST |
| P10.source_switch_count | >= 1 | null | TEST |

### Nice to Have (bonus differentiation)
| Check | Target | Current | Gap |
|---|---|---|---|
| P06.reconnect_success_rate | 100% | null | TEST |
| P06.session_resumption | 100% | null | TEST |
| P13.memory_recall | >= 1 | 1 | OK |
| P13.checkpoints_saved | >= 1 | 3 | OK |

---

## Overall Goal: 19/49 -> 35+/49 checks passing before submission

Current auto pass rate: 41.3%. Target: 70%+.

**Minimum viable for confident demo:** Fix F1 (question balance) + test T1 (interruption) + test T2 (grounding) = unblocks Hero Flow (POC 99).
