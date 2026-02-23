# POC 02 — Proactive Vision: Test Guide

## Setup

```bash
cd pocs/02_proactive_vision
uvicorn main:app --reload --port 8200
# Open http://localhost:8200
```

Ensure your environment has Application Default Credentials configured
(`gcloud auth application-default login`).

---

## Test Scenarios (from PRD)

### Test 1 — Goal Setting (M0)

**Steps:**
1. Click **Start Session** (mic + camera activate)
2. Wait for the tutor to greet you
3. Do NOT state a goal — let the tutor lead

**Pass criteria:**
- Tutor proactively asks "What are we working on?" or proposes a goal
  based on what it sees on camera
- Tutor confirms done-criteria before proceeding

**Observe:** Transcript shows tutor initiating goal-setting without prompting.

---

### Test 2 — Baseline Proactive Trigger (M1, M3, M5)

**Steps:**
1. Start session, establish a goal (e.g., "Let's work on this math problem")
2. Point camera at a worksheet with a visible error
3. Stay **completely silent** for 6–15 seconds
4. Watch the silence bar fill up

**Pass criteria:**
- Around 6s: backend sends a soft observation poke (event log)
- Around 9s: hard backend nudge fires if the tutor still has not spoken (cyan in transcript)
- Tutor speaks up with one concise intervention (observation, hint, or question) (purple "PROACTIVE" in transcript)
- Tutor references what it sees: "I notice you wrote..." / "Looking at line 2..."
- Comment is aligned with the established goal
- **Proactive Triggers** metric increments

**Fail indicators:**
- Tutor stays silent past 20s → prompt tuning needed
- Tutor gives the answer directly → system prompt violation
- Tutor mentions multiple issues → progressive disclosure failure
- Nearly every tutor turn ends with a question mark → follow-up fatigue risk

---

### Test 3 — Progressive Disclosure (M2)

**Steps:**
1. Start session, establish a goal
2. Show a worksheet with **3+ distinct errors**
3. Stay silent, wait for proactive trigger

**Pass criteria:**
- Tutor mentions **only the first/most important** error
- Does NOT list all errors at once
- After you address the first error, tutor moves to the next one

**How to verify:** Count distinct issues in the tutor's first proactive utterance.
The **Proactive Triggers** metric should show 1 trigger, and the transcript
should show only 1 topic addressed.

---

### Test 4 — False Alarm Test (M1)

**Steps:**
1. Start session with goal established
2. Click **Camera Off** to disable the camera
3. Point at a blank desk (or cover camera) — stay silent for 20s

**Pass criteria (camera off):**
- Tutor asks a general check-in ("How are you doing?") or stays silent
- Does NOT comment on visual work
- **False Positives** metric should remain at 0

**Steps (variant — blank desk with camera on):**
1. Camera on, pointing at a blank desk/wall
2. Stay silent for 20s

**Pass criteria (blank desk):**
- Tutor does NOT hallucinate work that isn't there
- May ask: "I don't see any work on camera — are you ready?"

---

### Test 5 — No Audio Overlap (M4)

**Steps:**
1. Start session, establish a goal
2. Point camera at work with errors
3. Start **talking continuously** (describe what you're doing, read aloud, etc.)
4. Keep talking for 30+ seconds

**Pass criteria:**
- Tutor listens silently while you speak
- Does NOT interrupt to comment on visuals
- **Silence bar** stays at 0 (no silence accumulation while speaking)
- After you stop talking, tutor may then comment

**Observe:** VAD events in event log should show continuous "speech START/END"
cycles. No proactive triggers should fire while student is speaking.

---

### Test 6 — Explicit Closeout (M7)

**Steps:**
1. Start session, establish a goal with clear done-criteria
2. Work through the problem with the tutor's guidance
3. Complete the done-criteria (show correct answer on camera)

**Pass criteria:**
- Tutor explicitly confirms: "Looks like you've got it!"
- Provides a recap of 1–3 key points learned
- Asks about next goal: "Want to tackle something else?"
- Session doesn't just peter out — there's a clear closure moment

---

### Test 7 — Follow-Up Restraint (S3 UX)

**Steps:**
1. Run a normal 5+ minute session (goal set, at least one proactive trigger)
2. Save transcript and count tutor turns ending in `?`
3. Count the longest streak of consecutive tutor turns ending in `?`

**Pass criteria:**
- **Question Turn Ratio** is between 35-50%
- Longest consecutive question streak is <= 2 turns
- Tutor still remains helpful (no direct-answer dumping)

**Quick check command:**
```bash
awk -F'Tutor: ' '/Tutor: /{t++; if($2 ~ /\\?/ ) {q++; s++} else {if(s>m) m=s; s=0}} END{if(s>m)m=s; printf "tutor_turns=%d question_turns=%d ratio=%.2f max_streak=%d\n", t,q,(t? q/t:0),m}' logs/transcript.log
```

---

## Metrics to Monitor

| Metric | Where | What it means |
|---|---|---|
| Proactive Triggers | Purple counter | Times tutor spoke without student prompt |
| Backend Nudges | Cyan counter | Hidden prompts injected by idle orchestrator |
| Organic / Nudge | Green counter | Split: triggers from prompt vs. from nudge |
| Avg Trigger (s) | Purple counter | Mean silence duration before trigger |
| Turns | Blue counter | Total conversation turns |
| False Positives | Orange counter | Tutor spoke visually with camera off |
| Video FPS | Counter | Confirms camera frames are flowing |
| Silence Bar | Progress bar | Live countdown to nudge threshold |
| Question Turn Ratio | Transcript analysis | `% of tutor turns ending with ?` (target 35-50%) |
| Question Streak Max | Transcript analysis | Longest consecutive question-ending tutor turn streak (target <= 2) |

---

## Success Criteria Summary

| ID | Criterion | Target |
|---|---|---|
| M0 | Goal Setting | Tutor asks for/proposes goal at session start |
| M1 | Reliable Proactive Trigger | 100% trigger rate in 20s silent windows with camera |
| M2 | Progressive Disclosure | 1 concept per proactive utterance |
| M3 | Helpfulness | Socratic guidance (questions/hints), never direct answers |
| M4 | No Audio Overlap | 0 proactive comments during student speech |
| M5 | Goal Alignment | Comments reference stated goal |
| M6 | Mission-Control Flow | Goal → Grounding → Plan → Execute → Closeout |
| M7 | Explicit Closeout | Tutor confirms completion + summarizes |
| S3 | Follow-Up Restraint (UX) | Question-ending turns stay in 35-50% range; max streak <= 2 |

---

## Troubleshooting

| Issue | Fix |
|---|---|
| Camera shows "Camera off" | Check browser permissions (Settings → Privacy → Camera) |
| No audio from tutor | Check browser autoplay policy; click in the page first |
| Silence bar not filling | Ensure VAD is ON and you're not making any noise |
| Backend nudge never fires | Check server logs for "IDLE NUDGE"; verify camera frames are flowing (Video FPS > 0) |
| Model errors | If using `gemini-live-2.5-flash-native-audio` and video fails, switch to `gemini-2.0-flash-live-preview-04-09` in main.py |

---

## Log Files

Session logs are saved to `pocs/02_proactive_vision/logs/`:
- `{timestamp}_{session_id}.jsonl` — Raw events (machine-readable)
- `details.log` — Human-readable event log (newest session, newest-first)
- `transcript.log` — Conversation transcript (newest session, newest-first)
