# To Be Fixed & To Be Tested

## Session Log

| Session | Date | Duration | Student/Tutor turns | Camera | Key observation |
|---|---|---|---|---|---|
| `85c3966f` | Mar 1 19:53 | 6.8 min | 26 / 41 | OFF | Baseline — question ratio 91%, no video |
| `678a4c76` | Mar 1 21:06 | 4.6 min | 17 / 26 | ON (254 frames) | Active convo — proactive correctly skipped |
| `678a4c76` | Mar 1 21:22 | ~2 min | 0 / 1 | ON (screen share) | **Silent test — proactive FAILED despite 37s silence** |

---

## To Be Fixed

### F1. Question-ending ratio too high
- **Severity:** High — directly impacts student experience
- **Symptom:** Session 85c3: 91.3%, streak of 16. Session 678a: 66.7%, streak of 3. Student said "No, don't rush. I need to digest this."
- **Root cause:** System prompt over-indexes on Socratic questioning without enough variety
- **Fix:** Tune system prompt to mix in statements, encouragement, summaries, and pauses. Add explicit instruction like "After 2 consecutive questions, give a statement or encouragement before asking again."
- **Metric:** `derived_metrics.question_turn_ratio_percent` + `max_question_streak`
- **Target:** Ratio 35-50%, streak <=2
- **Status:** Improved in 678a (66.7%) without code change — topic-dependent variance. Still needs prompt fix to be reliably in range.

### F2. Response start latency over target
- **Severity:** Medium — noticeable dead air
- **Symptom:** Session 85c3: avg 787ms (target <=500ms), p95 1,590ms (target <=800ms)
- **Possible causes:** Model inference time, context window size, tool call overhead
- **Fix:** Profile where time is spent. Consider trimming system prompt size, reducing injected context per turn, or pre-warming the session.
- **Metric:** `latency.response_start.avg` / `.p95`

### F3. Frequent silence gaps (5-17s with no Gemini response)
- **Severity:** Medium — dead air while student hears nothing
- **Symptom:** Both sessions show repeated SILENCE alerts (no Gemini events for 5-17s despite ~47 audio chunks/interval being sent). Confirmed in 678a with video also flowing.
- **Possible causes:** Model processing delay, context window saturation, tool calls blocking audio pipeline
- **Fix:** Investigate if silence correlates with context size growth. Consider "thinking" indicator on frontend, or backend nudge after N seconds of silence.

### F4. Tutor gave incorrect grammar correction
- **Severity:** Medium — factual accuracy in tutoring
- **Symptom:** Tutor said `sein**e** Buch` for neuter noun, but correct German is `sein Buch` (neuter nominative = no ending on `sein`).
- **Fix:** Model hallucination issue. Add grounding/search for grammar rules, or guardrail to flag grammar corrections with low confidence.

### F5. Proactive vision not triggering during silence — **CONFIRMED BUG**
- **Severity:** High — core POC 02 feature is broken
- **Symptom:** Session 678a (21:22): Student shared screen, stayed completely silent for 37+ seconds. Video frames flowing (3-6/heartbeat), mic=True, speaking=False, away=False. Poke threshold is 6s. **Zero pokes fired.** User reported bad experience ("I was expecting some interventions").
- **Root cause (confirmed via code trace):**
  1. **Primary:** `_is_probable_speech_pcm16()` (main.py:2571) uses very low thresholds (`rms >= 420 or peak >= 1700`) to detect speech in raw PCM audio. Ambient mic noise triggers this heuristic, which calls `reset_silence_tracking()` (main.py:3240) up to once per second. This zeroes `silence_started_at`, so the proactive module's silence counter **never reaches the 6s threshold**.
  2. **Secondary:** `conversation_started` is set to `False` on `mic_start` (main.py:3016). The mic kickoff requires `idle_for >= 5s`, but the same speech heuristic keeps resetting `last_user_activity_at`, delaying kickoff by ~18s. While `conversation_started=False`, the proactive module skips entirely (proactive.py:200).
  3. **Tertiary:** Non-suppressed `TURN_COMPLETE` events (main.py:3904-3906) also reset silence tracking every ~3s during the suppressed output burst at 21:23:27-21:23:38.
- **Fix plan (two changes needed):**
  1. **Stop speech heuristic from resetting proactive silence:** At main.py:3240, do NOT call `reset_silence_tracking()` from the PCM heuristic. The proactive silence counter should only reset on confirmed student speech (`input_transcription` events), not on raw PCM noise. Keep resetting `last_user_activity_at` for idle/away detection.
  2. **Raise speech heuristic thresholds:** `rms >= 420` and `peak >= 1700` are too sensitive — typical ambient noise on a laptop mic exceeds these. Raise to `rms >= 800` and `peak >= 3000` or similar.
- **Optional improvement:** Give proactive module its own independent silence timer that only resets on `input_transcription`, fully decoupled from the shared `silence_started_at`.
- **Metric:** `proactive.poke_count >= 1` after 10s silence with video active
- **Status:** Root cause identified. Fix ready to implement.

### F6. TURN_DROPPED flood — wasted compute (**NEW**)
- **Severity:** Low-Medium — no user-facing bug, but wasted tokens
- **Symptom:** Session 678a debug log shows ~12 `TURN_DROPPED_START` / `TURN_DROPPED_COMPLETE` pairs. Gemini generates extra turns that the ticket gate correctly blocks, but the generation still consumes tokens and may add latency.
- **Possible causes:** Model is overly chatty; system prompt encourages follow-ups; ticket system works but model doesn't know it's being gated.
- **Fix:** Consider sending a `send_activity_end()` or similar signal when a turn is dropped to tell Gemini to stop generating. Or add system prompt instruction to wait for student input before speaking again.

---

## To Be Tested

### T1. Proactive vision (POC 02) — **BLOCKED on F5 fix**
- **Previous status:** Tested on Mar 1 21:22 — FAILED. 37s silence with screen share, zero pokes.
- **Blocked by:** F5 (speech heuristic resets silence tracking). Must fix F5 first, then re-test.
- **How to re-test after fix:** Share camera or screen showing homework, then **stay silent for 10+ seconds**. Don't talk — let the tutor initiate.
- **Pass criteria:** poke_count >= 1, nudge_count >= 0

### T2. Interruption handling (POC 01) — still needs deliberate test
- **What:** Actively interrupt the tutor mid-speech and verify it stops + acknowledges
- **Why:** Session 678a had 0 real interruptions and 12 stale-filtered (tutor already silent when interrupt arrived). Tutor speaking windows are short (~2s), making accidental interruption unlikely.
- **How:** Start speaking loudly and clearly while tutor is mid-sentence. Check `interruptions.count >= 1`.
- **Tip:** The tutor's speaking bursts are brief (1-3s from SPEAKING_START to TURN_COMPLETE). You need to talk DURING that window, not after.
- **Pass criteria:** interruptions.count >= 1, interruption_stop p95 <= 500ms

### T3. Search grounding / citations (POC 05) — not tested
- **What:** Ask a factual question that should trigger grounding search
- **Why:** 0 grounding events across both sessions
- **How:** Ask something like "What is the rule for dative case in German?" or a fact the tutor should look up
- **Pass criteria:** grounding.events >= 1, citations_sent >= 1

### T4. Screen share toggle (POC 10) — not tested
- **What:** Switch between camera and screen share during a session
- **Why:** 0 source switches across both sessions
- **How:** Start with camera, switch to screen share, then stop sharing. Verify no errors.
- **Pass criteria:** source_switches >= 1, stop_sharing_count >= 1, errors = []

### T5. Idle / away flow (POC 11) — partially tested
- **What:** Go silent long enough to trigger away mode, then resume
- **Why:** Session 85c3 had 1 gentle checkin but away never activated. Session 678a had 0 checkins (session too short).
- **How:** Stop talking for 2+ minutes, verify away_activated fires. Then speak again, verify away_resumed.
- **Pass criteria:** away_activated_count >= 1, away_resumed_count >= 1

### T6. Session resumption (POC 06) — not tested
- **What:** Disconnect and reconnect with resumption enabled
- **Why:** `resumption_enabled: false` in both sessions
- **How:** Enable resumption in config, start session, kill WS, reconnect. Verify context preserved.
- **Pass criteria:** session_resume_successes >= 1

### T7. Multilingual purity (POC 03) — not tested
- **What:** Run a full session in one language and measure purity rate
- **Why:** Language purity metric was null in both sessions
- **How:** Run a German-only session or explicitly set language preference.
- **Pass criteria:** language_purity_rate >= 98%

### T8. Question streak after fix (validates F1)
- **What:** After fixing the system prompt (F1), re-run and verify question ratio dropped
- **How:** Run same type of session, check scorecard
- **Pass criteria:** question_turn_ratio 35-50%, max_question_streak <= 2

---

## Updated Priority Order

1. **F5** (proactive vision) — **fix NOW**, core POC 02 feature is broken. Two code changes in main.py.
2. **T1 + T2** (combined test session) — re-test proactive after F5 fix + interrupt test. One session, two tests.
3. **F1** (question ratio) — system prompt tweak, biggest UX win
4. **F2/F3** (latency/silence) — profile and investigate
5. **F6** (turn dropped flood) — investigate if it's contributing to F2/F3
6. **T3-T7** (remaining POC coverage)
7. **F4** (grammar accuracy) — model-level, harder to fix
8. **T8** (validate F1 fix)
