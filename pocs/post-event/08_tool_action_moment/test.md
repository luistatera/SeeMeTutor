# POC 08 -- A2A Session Summary: Test Plan

## How to Run

```bash
cd pocs/08_tool_action_moment
uvicorn main:app --reload --port 8800
# Open http://localhost:8800
```

**Prerequisites:**
- Vertex AI authentication configured (`gcloud auth application-default login`)
- `GOOGLE_GENAI_USE_VERTEXAI=TRUE` (set automatically by the backend)
- Python dependencies: `fastapi`, `uvicorn`, `google-genai`, `python-dotenv`

---

## Test Scenarios

### Test 1: Fast Disconnect (M0)

**Goal:** WebSocket closes immediately on session end, no blocking for summary.

**Steps:**
1. Open http://localhost:8800
2. Click "Start Session"
3. Say a few words (e.g., "Hi, can you help me with fractions?")
4. Wait for at least one tutor response
5. Click "End Session"

**Pass criteria:**
- WS Close Latency metric card shows < 1000ms
- Status changes to "Generating study guide..." immediately
- No lag or freeze in the UI
- Event log shows "WS disconnected" promptly after "Ending session"

---

### Test 2: Transcript Extraction + A2A Trigger (M1)

**Goal:** Orchestrator extracts transcript and triggers the Reflection Agent.

**Steps:**
1. Start a session
2. Have a 3+ turn conversation (ask about a math topic, respond to tutor questions)
3. Click "End Session"

**Pass criteria:**
- Server logs show: `triggering Reflection Agent (transcript=N entries, student=...)`
- N > 0 (transcript was accumulated)
- Server logs show: `REFLECTION AGENT started for session ...`
- Transcript entries include both student and tutor lines
- Check `logs/` directory for the session JSONL file containing transcript events

---

### Test 3: Valid JSON Output (M2)

**Goal:** Reflection Agent produces valid JSON matching the schema.

**Steps:**
1. Complete a session with at least 2-3 turns of conversation
2. Click "End Session"
3. Wait for the study guide to appear

**Pass criteria:**
- Server logs show: `REFLECTION AGENT completed for session ... in X.XXs`
- Study guide panel displays all 5 sections:
  - Mastered Concepts (green left border)
  - Areas to Practice (amber left border, each with topic + detail)
  - Next Steps (blue left border, numbered)
  - Session Summary (text box)
  - Encouragement (teal box, italic)
- A `logs/summary_poc8-XXXXX.json` file exists and contains valid JSON
- JSON contains all required fields: `mastered_concepts`, `struggle_areas`, `next_steps`, `session_summary`, `encouragement`

**REST verification:**
```bash
curl http://localhost:8800/summaries
curl http://localhost:8800/summary/poc8-XXXXXXX
```

---

### Test 4: Summary Accuracy (M4)

**Goal:** Summary reflects actual transcript content, no hallucinated topics.

**Steps:**
1. Start a session
2. Discuss a specific topic (e.g., "I need help with multiplying fractions")
3. Engage in 3-5 turns about that topic
4. Click "End Session"
5. Read the generated summary

**Pass criteria:**
- Mastered concepts only reference topics actually discussed
- Struggle areas reference actual mistakes or confusion from the conversation
- Next steps are relevant to the discussed topic
- Session summary accurately describes what happened
- No mentions of topics that were never discussed
- Encouragement references something the student actually did

---

### Test 5: Generation Timing (< 10s)

**Goal:** Summary generation completes in under 10 seconds.

**Steps:**
1. Complete any session (short or long)
2. Click "End Session"
3. Observe the spinner elapsed timer and "Summary Gen Time" metric card

**Pass criteria:**
- Spinner shows real-time elapsed counter while generating
- Generation time shown is under 10 seconds
- Typical expected: 2-5 seconds
- A2A Handoff timing badge shows green (< 5s) or purple (5-10s), not amber (> 10s)
- Server logs confirm: `REFLECTION AGENT completed ... in X.XXs` where X < 10

---

### Test 6: Empty Session Handling

**Goal:** Graceful handling when session ends with no conversation.

**Steps:**
1. Click "Start Session"
2. Immediately click "End Session" (before speaking)
3. Wait for summary

**Pass criteria:**
- No crash or error
- Summary generates successfully (possibly with empty lists)
- Session summary notes the session was empty or very brief
- No stack traces in server logs

---

### Test 7: Model Routing (S1)

**Goal:** Prove that the Reflection Agent uses a different model than the Live Tutor.

**Steps:**
1. Complete any session and end it
2. Check the timing badges in the study guide

**Pass criteria:**
- Two model badges visible: "Live: gemini-live-2.5-flash-native-audio" and "Reflection: gemini-2.0-flash"
- Server logs show the Live session uses `gemini-live-2.5-flash-native-audio`
- Server logs show the Reflection Agent uses `gemini-2.0-flash` via `generate_content`
- REST response at `/summary/{id}` includes `model_used: "gemini-2.0-flash"`

---

### Test 8: Student Name Propagation

**Goal:** Student name from the input field appears in the summary.

**Steps:**
1. Type a custom name (e.g., "Sofia") in the student name field
2. Start and complete a session
3. Click "End Session"
4. Check the generated summary JSON

**Pass criteria:**
- `student_name` field in the summary JSON matches what was typed
- Server logs show: `student name set to 'Sofia'`

---

### Test 9: REST Endpoints

**Goal:** Summary REST API works correctly.

**Steps:**
```bash
# Before any session
curl http://localhost:8800/summaries
# Expected: {"summaries": []}

# After a session is generating
curl http://localhost:8800/summary/poc8-XXXXXXX
# Expected: 404 with {"status": "pending"} OR {"status": "generating"}

# After summary is ready
curl http://localhost:8800/summary/poc8-XXXXXXX
# Expected: Full JSON with status "ready"

curl http://localhost:8800/summaries
# Expected: List with 1+ entries
```

---

## Key Metrics to Track

| Metric | Target | Where to check |
|---|---|---|
| WS close latency | < 1000ms | "WS Close Latency" metric card + event log |
| Summary gen time | < 10s | "Summary Gen Time" metric card + server logs |
| JSON parse success | 100% | Server logs (no parse errors) |
| Transcript accuracy | Manual review | Compare summary sections to transcript |
| No hallucinated topics | Manual review | Read summary sections |

## Architecture Verification

- **Phase 1 (Live):** Uses `gemini-live-2.5-flash-native-audio` via Gemini Live API (WebSocket, audio modality)
- **Phase 2 (Reflection):** Uses `gemini-2.0-flash` via standard text API (`generate_content`, JSON response)
- **A2A Handoff:** Background `asyncio.create_task` triggers after WebSocket closes
- **Persistence:** Summary saved to `logs/summary_{session_id}.json` (simulating Firestore)
