# SeeMe Tutor — Pivot to General Study Companion

**Date:** 2026-03-01
**Source:** Voice conversation (Feb 28) — strategic pivot discussion
**Deadline:** March 16, 2026 @ 5:00 PM PDT (15 days remaining)

---

## The Pivot

**Before:** SeeMe is a language-learning tutor with L1/L2 drill modes, guided bilingual phases, immersion mode, recap cycles, and confusion fallback — essentially a language teacher.

**After:** SeeMe is a **general-purpose study companion**. A student loads a profile, picks a topic they're studying (a book, a course, a chapter), and the app searches for context about that topic. During the session, when the student shares exercises via camera or screen share, the app already knows the domain context, so it can guide the student effectively.

**The core challenge:** Make sure the student actually **masters** the topic — not just rush through exercises and mark them done.

**What this means architecturally:**
- The language module's learning-specific features (guided_bilingual, immersion, L2 drills, recap cycles) must be **removed** — they're for teaching languages, not for general study help
- Language support stays, but simplified: detect what language the student speaks, respond in that language
- Google Search becomes the context engine: when a student sets up a topic ("German A2 textbook, chapter 5"), the app searches and loads relevant context
- The mastery system must be redesigned around **verified understanding**, not checkbox completion

---

## Demo Strategy

Three profiles, three completely different use cases, one system:

| Profile | Student | Subject | Level | Language |
|---------|---------|---------|-------|----------|
| `luis-german` | Luis | German A2 (textbook exercises) | Adult learner | EN or DE |
| `sofia-math` | Daughter | French & Math | Grade 4 | PT or FR |
| `ana-chemistry` | Stepdaughter | University Chemistry | Year 1 | PT |

Judges see: same app, different domains, different languages, different skill levels. No books needed — the app has context loaded. The student just opens the app, picks their profile, and starts studying.

---

## Part 1: Remove Language-Learning-Specific Code

The language module (`backend/modules/language.py`, ~1083 lines) is currently built for **teaching languages**. Most of it must be stripped out or simplified.

### What to REMOVE

These features exist to teach a language through structured L1/L2 alternation — irrelevant for a general study companion:

| Feature | Location | Why remove |
|---------|----------|------------|
| `guided_bilingual` mode | `language.py` lines 419-427, `build_language_contract()` | Drills in L2, explains in L1 — language teaching, not study help |
| `immersion` mode | `language.py` lines 428-434 | Forces L2-only responses — language teaching |
| L2 streak tracking | `language.py` lines 978-981, `init_language_state()` | Counts consecutive L2 turns for recap trigger — language teaching |
| Recap triggers | `language.py` lines 986-1007, `finalize_tutor_turn()` | Forces L1 recap after N L2 turns — language teaching |
| Guided phase switching | `language.py` lines 1009-1038, `finalize_tutor_turn()` | Alternates explain/practice phases — language teaching |
| Confusion fallback (language-specific) | `language.py` lines 840-871, `handle_student_transcript()` | Falls back to L1 after repeated confusion — assumes language learning context |
| L1/L2 word ratio metrics | `language.py` lines 942-949 | Tracks L1 vs L2 word counts — only meaningful for language learning |
| `language_guided_phase` state keys | `init_language_state()` lines 626-628 | Phase state for guided bilingual — not needed |
| `language_force_language_key` | `init_language_state()` lines 629-630 | Forced language locks — language teaching mechanism |
| `SUPPORTED_LANGUAGE_MODES` (guided_bilingual, immersion) | line 20 | Only `auto` remains |

### What to KEEP (simplified)

| Feature | Why keep |
|---------|----------|
| `auto` mode | Detect student's language, respond in that language — works for any subject |
| `detect_language()` | Still need to know what language the student is speaking |
| `analyze_turn_language()` | Still useful for metrics (is tutor responding in correct language) |
| Basic `build_language_contract()` | Simplified: "Respond in the student's language. One language per turn." |
| `language_label()` / `language_short()` | Utility functions, still needed |
| `init_language_state()` (simplified) | Still need basic language state, just fewer keys |
| `append_tutor_text_part()` | Still needed for turn analysis |

### System prompt changes (agent.py)

**Remove** from `_BASE_INSTRUCTION` (lines 80-101):

```
# REMOVE this entire paragraph:
"For guided bilingual language learning (for example German A2): explain
strategy in L1, run drills in L2, then return to a short L1 recap based on the
contract settings. Gently correct errors by modeling the correct form in a
follow-up question, not by stating 'that was wrong.'"
```

**Replace** the Language Matching section with:

```
## Language Matching

Respond in the same language the student uses. If the student speaks German,
respond in German. If they speak Portuguese, respond in Portuguese. If their
language is unclear, ask briefly which language they prefer.

One language per turn — never mix languages in the same response.
```

### Impact on `main.py`

- Remove calls to guided bilingual / immersion logic in the WS receive loop
- Simplify `_load_backlog_context()`: language_policy only needs `mode: "auto"` + `l1`
- Remove `_apply_language_policy_templates()` if it only handled bilingual/immersion templates
- Keep `handle_language_student_transcript` and `finalize_language_tutor_turn` calls but they'll be simpler internally

### Impact on `test_report.py` and tests

- `test_language.py` tests for guided_bilingual, immersion, recap, confusion_fallback → update or remove
- PRD scorecard metrics for `l2_ratio` → remove (no longer meaningful)
- `fix_and_test_prd_scorecard.md` Bug 2 (L2 ratio) → no longer applies

### Files to change
- `backend/modules/language.py` — major simplification
- `backend/agent.py` — system prompt language section rewrite
- `backend/main.py` — simplify language policy loading/wiring
- `backend/tests/test_language.py` — update tests
- `backend/test_report.py` — remove L2-specific metrics

### Effort: ~3 hours

---

## Part 2: Topic Context via Google Search

**Goal:** When a student sets up a topic ("German A2, Chapter 5: Dative Case"), the app uses Google Search to load relevant context. This context is stored and injected into every session so the tutor is knowledgeable about the specific material.

### How it works

1. **Profile setup** (seed script or future UI): student record includes a `topic_context_query` per topic
   ```
   topics/{topic_id}/
     ├── title: "Dative Case"
     ├── context_query: "German A2 dative case articles exercises rules"
     ├── context_summary: ""  # populated by search
     └── ...
   ```

2. **Context loading** (new utility or on first session):
   - If `context_summary` is empty, use `google_search` (already available as an ADK tool) to search for `context_query`
   - Store a summary of the search results in `context_summary`
   - This runs once per topic, not every session

3. **Session injection** (`_load_backlog_context` in `main.py`):
   - Include `topic_context_summary` in the backlog context
   - Pass it into `[SESSION START]` hidden turn
   - Tutor now knows the domain before the student even speaks

### New agent tool: `search_topic_context`

```python
async def search_topic_context(query: str, tool_context: ToolContext) -> dict:
    """Search for educational context about the current study topic.

    Call this when the student describes what they're studying and you need
    more context about the subject to help effectively.

    Args:
        query: Search query about the study topic (e.g., "dative case German grammar rules")
    """
    # Delegates to google_search internally
    # Stores results summary in session state + Firestore topic
```

This tool can also be called mid-session if the student shifts to a sub-topic the tutor needs more context on.

### System prompt addition (tutoring phase):

```
### Topic Context Awareness

At session start, you receive context about the student's current study topic.
Use this context to guide the student — reference specific rules, formulas,
or concepts from the loaded material.

If the student shows exercises on camera that go beyond your loaded context,
call `search_topic_context` to learn more before guiding them.

When the student describes a new topic or book they want to study, call
`search_topic_context` to load context, then help them.
```

### Files to change
- `backend/agent.py` — new tool + system prompt update
- `backend/main.py` — extend `_load_backlog_context()` to include topic context
- `backend/seed_demo_profiles.py` (NEW) — include `context_query` per topic

### Effort: ~2 hours

---

## Part 3: Mastery Verification System (The Core Challenge)

**This is the most important part.** The current system lets the tutor mark a topic as "mastered" after minimal verification. For a real study companion, mastery must be **proven**, not assumed.

### The Problem

Current flow:
1. Student answers exercise correctly
2. Tutor calls `update_note_status(note_id, "done")` or `log_progress(topic, "mastered")`
3. Move on

This doesn't verify understanding. The student might have guessed, copied, or understood the specific problem but not the concept.

### The Solution: 3-Step Mastery Protocol

Before marking ANY exercise or topic as "mastered", the tutor must verify understanding through three steps:

```
Step 1: SOLVE — Student solves the exercise correctly
Step 2: EXPLAIN — Student explains WHY their answer is correct (tests understanding)
Step 3: TRANSFER — Student solves a similar but different problem (tests generalization)

Only after all 3 steps → mark as mastered
```

### Implementation

#### A. System prompt (tutoring phase) — add Mastery Protocol section:

```
### Mastery Verification Protocol

You MUST follow this protocol before marking any exercise as mastered:

**Step 1 — SOLVE:** Guide the student to the correct answer using Socratic method.
When they get it right, celebrate, then move to Step 2.

**Step 2 — EXPLAIN:** Ask the student to explain their reasoning:
- "Great answer! Can you explain why that works?"
- "How did you know to use that formula?"
- "What's the rule behind this?"

If they can't explain → they haven't mastered it. Go back to teaching the concept,
then try Step 1 again with the same or simpler problem.

If they explain correctly → move to Step 3.

**Step 3 — TRANSFER:** Give a similar problem with different numbers/context:
- "Now try this one: [variation of the same concept]"
- "What if the number was negative instead?"
- "Apply the same rule to this sentence: [different example]"

If they solve the transfer problem → call update_note_status(note_id, "mastered").
If they struggle → the concept isn't fully internalized. Teach the gap, then retry.

**CRITICAL:** Never skip steps. Never mark mastered after just Step 1. The whole
point of this app is to ensure REAL understanding, not checkbox completion.

**Progress signals to the student:**
- After Step 1: "You got it! Now tell me — why does that work?"
- After Step 2: "You really understand this. Let me give you one more to be sure..."
- After Step 3: "Boom — you've mastered this one! That concept is yours now."
```

#### B. Agent tool update — `update_note_status`

Add validation to prevent premature mastery marking:

```python
# In update_note_status, add a mastery verification state machine
# Track: which step of the protocol is the student on?

# Session state per exercise:
# mastery_step_{note_id}: "solve" | "explain" | "transfer" | "verified"

# When tutor calls update_note_status(note_id, "mastered"):
#   - Check mastery_step_{note_id}
#   - If not "verified" → return warning, don't mark mastered
#   - If "verified" → proceed
```

#### C. New tool: `verify_mastery_step`

```python
def verify_mastery_step(note_id: str, step: str, passed: bool,
                        tool_context: ToolContext) -> dict:
    """Record that the student passed or failed a mastery verification step.

    Args:
        note_id: The exercise being verified.
        step: Which step — "solve", "explain", or "transfer".
        passed: Whether the student passed this step.
    """
    state_key = f"mastery_step_{note_id}"
    if passed:
        if step == "solve":
            tool_context.state[state_key] = "explain"  # next step
            return {"result": "step_passed", "next_step": "explain",
                    "prompt": "Ask the student to explain WHY their answer works."}
        elif step == "explain":
            tool_context.state[state_key] = "transfer"
            return {"result": "step_passed", "next_step": "transfer",
                    "prompt": "Give the student a similar problem with different values."}
        elif step == "transfer":
            tool_context.state[state_key] = "verified"
            return {"result": "mastery_verified",
                    "prompt": "Student has verified mastery. You may now call update_note_status with 'mastered'."}
    else:
        # Failed step — go back
        tool_context.state[state_key] = "solve"
        return {"result": "step_failed", "step": step,
                "prompt": f"Student didn't pass the {step} step. Reteach the concept and try again."}
```

#### D. Metrics for mastery quality

Track in `test_report.py`:
- `mastery_verifications`: how many 3-step verifications completed
- `premature_mastery_blocked`: how many times tutor tried to mark mastered without verification
- `average_steps_to_mastery`: mean number of attempts before mastery
- `explain_pass_rate`: % of explain steps passed on first try
- `transfer_pass_rate`: % of transfer steps passed on first try

### Files to change
- `backend/agent.py` — mastery protocol in system prompt + `verify_mastery_step` tool
- `backend/agent.py` — update `update_note_status` to check verification state
- `backend/test_report.py` — mastery quality metrics
- `backend/tests/` — new test for mastery protocol

### Effort: ~3 hours

---

## Part 4: Demo Profiles with Pre-loaded Context

**Goal:** One-command script seeds 3 student profiles in Firestore with tracks, topics, context queries, and pre-searched context summaries. Judges pick a profile and start studying immediately.

### Seed script: `backend/seed_demo_profiles.py`

```bash
python backend/seed_demo_profiles.py
# Creates 3 students with ready-to-use study profiles
```

**Profile 1: Luis — German A2**
- Track: "German A2"
- Topics: Dative case, Perfekt tense, Modal verbs, Wechselpräpositionen
- Context queries: "German A2 dative case rules and exercises", etc.
- Language: auto (responds in whatever language Luis speaks)
- Tutor prefs: balanced pace, medium socratic intensity

**Profile 2: Sofia — French & Math**
- Track: "Grade 4 French & Math"
- Topics: Multiplication tables, Fractions intro, French verb conjugation (présent), Vocabulary (animals)
- Context queries: "grade 4 multiplication word problems", "French present tense conjugation rules", etc.
- Language: auto
- Tutor prefs: slower pace, high encouragement

**Profile 3: Ana — University Chemistry**
- Track: "General Chemistry I"
- Topics: Atomic structure, Chemical bonding, Stoichiometry, Acid-base
- Context queries: "university general chemistry atomic structure", etc.
- Language: auto
- Tutor prefs: faster pace, detailed explanations, high socratic intensity

### Files to create
- `backend/seed_demo_profiles.py`

### Effort: ~2 hours

---

## Part 5: SESSION START Context Update

**Goal:** Inject topic context into the session so the tutor starts knowledgeable.

### Changes to `main.py` → `_load_backlog_context()`

Add to the returned context dict:
```python
"topic_context_query": topic_data.get("context_query", ""),
"topic_context_summary": topic_data.get("context_summary", ""),
```

### Changes to `main.py` → SESSION START hidden turn

Update the student context injected at session start:
```python
student_context = {
    "student_name": ...,
    "preferred_language": ...,
    "resume_message": ...,
    "topic_title": ...,
    "topic_status": ...,
    "language_contract": ...,  # simplified — just "auto" mode
    "tutor_preferences": ...,
    "previous_notes_count": ...,
    # NEW:
    "topic_context_summary": session_state.get("topic_context_summary", ""),
    "track_title": session_state.get("track_title", ""),
}
```

### Changes to `agent.py` → greeting phase

Update `_PHASE_GREETING` to reference topic context:
```
If topic_context_summary is provided, you already know about this topic.
Reference it naturally: "I see you're studying {topic_title}. Last time we..."
```

### Files to change
- `backend/main.py` — backlog context + SESSION START
- `backend/agent.py` — greeting + tutoring phase prompts

### Effort: ~1 hour

---

## Part 6: Judge Experience

### Profile selector polish (frontend)

The frontend already has a profile picker. Update it with:
- Clear card descriptions: "Luis — Studying German A2" with subject icon
- One-click start: select profile → session begins
- No login, no account creation — just pick and study

### README quick start

```
## Quick Start (for Judges)

1. Open the app: [live URL]
2. Pick a student profile (3 pre-loaded)
3. Start talking — ask about the current topic, show exercises on camera, or just chat
4. The tutor knows the study context and will guide you through exercises
5. Try the mastery check: solve an exercise, then see if the tutor asks you to explain and transfer
```

### Files to change
- `frontend/index.html` — profile card descriptions
- Root `README.md` — quick start section

### Effort: ~1.5 hours

---

## Execution Order

```
Phase 1 — Strip language-learning code (Day 1)           ~3 hours
  ├─ Simplify language.py (remove guided_bilingual, immersion, recaps)
  ├─ Update agent.py system prompt (remove L1/L2 drills language)
  ├─ Simplify main.py language policy loading
  ├─ Update tests
  └─ Verify: session still works with auto-language-only

Phase 2 — Mastery verification system (Day 2)            ~3 hours
  ├─ Add verify_mastery_step tool to agent.py
  ├─ Add mastery protocol to system prompt (tutoring phase)
  ├─ Update update_note_status to check verification state
  ├─ Add mastery metrics to test_report.py
  └─ Verify: tutor asks explain + transfer before marking mastered

Phase 3 — Topic context + search (Day 3)                 ~2 hours
  ├─ Add search_topic_context tool to agent.py
  ├─ Extend _load_backlog_context with topic context
  ├─ Update SESSION START injection
  └─ Verify: tutor references topic context in responses

Phase 4 — Demo profiles + seed script (Day 3-4)          ~2 hours
  ├─ Create seed_demo_profiles.py with 3 profiles
  ├─ Include context_query per topic
  └─ Verify: profiles load correctly in app

Phase 5 — Judge UX polish (Day 4)                        ~1.5 hours
  ├─ Update frontend profile cards
  ├─ Write README quick start
  └─ End-to-end test with all 3 profiles

Phase 6 — Integrate with remaining scorecard fixes (Day 5) ~2 hours
  ├─ Question streak fix (from fix_and_test_prd_scorecard.md Bug 1)
  ├─ Remove L2 ratio check (no longer applicable)
  ├─ Run structured test session
  └─ Verify auto_pass_rate ≥ 85%
```

**Total: ~13.5 hours across 5 days (Mar 2–6)**
Leaves 10 days for demo video, blog post, and submission polish.

---

## What We're NOT Doing

| Idea | Decision | Reason |
|------|----------|--------|
| Language teaching features (L1/L2 drills, immersion) | REMOVING | App is a study companion, not a language teacher |
| Dynamic RAG with Context Caching | Deferred | Google Search + stored summaries are sufficient |
| ML-based learning curve model | Deferred | 3-step mastery protocol gives visible adaptive behavior |
| User-created profiles (signup flow) | Deferred | Pre-seeded profiles for hackathon |
| Multiple simultaneous tracks per student | Deferred | One active track per profile is enough |

---

## Impact on Existing Plans

| Plan | Impact |
|------|--------|
| `fix_and_test_prd_scorecard.md` | Bug 2 (L2 ratio) is **obsolete** — remove. Bug 1 (question streak) still applies. Latency investigation still applies. |
| `context_caching_rag_plan.md` | Fully **deferred** — replaced by search-based context loading |
| `MIGRATION.md` Steps 4-8 | Unaffected — whiteboard sync and remaining steps are orthogonal |

---

## Success Criteria

- [ ] Language module stripped of learning-specific features; only auto-detect remains
- [ ] System prompt reframed as "study companion" not "language tutor"
- [ ] 3-step mastery protocol enforced (solve → explain → transfer → mastered)
- [ ] `verify_mastery_step` tool fires during sessions
- [ ] `search_topic_context` tool loads domain context
- [ ] 3 demo profiles seeded and working
- [ ] Judges can pick a profile and start studying in < 30 seconds
- [ ] No books or external materials needed
- [ ] PRD scorecard at 85%+ (adjusted for removed L2 metrics)
- [ ] Demo video shows mastery protocol in action across 3 profiles

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Stripping language.py breaks something subtle | Session crash | Run existing tests after each removal; keep auto mode path untouched |
| Mastery protocol is too rigid (tutor gets stuck in loops) | Bad UX | Add escape hatch: after 3 failed attempts at any step, offer to skip and return later |
| Google Search context is too shallow | Tutor gives generic advice | Pre-search during seed script; store rich summaries; tutor can search mid-session |
| 3-step protocol slows down demo | Judges get bored | Keep steps concise — protocol should take ~2 min per exercise, not 10 |
| Removing language features loses differentiation | Judges don't see multilingual capability | Keep auto-detect + respond-in-language; demo with 3 languages (DE, FR, PT) shows multilingual naturally |
