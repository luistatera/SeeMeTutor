# POC 05 — Search Grounding: Mini PRD

## Why This Matters

Hallucination avoidance is **explicitly scored** in the judging rubric.
From the criteria (30% weight — Technical Implementation & Agent Architecture):

> "hallucination avoidance and grounding evidence"

Most hackathon projects will ignore this line entirely. If SeeMe's tutor
visibly fact-checks itself using Google Search before teaching — and shows
judges a citation card proving it — we demonstrate grounding in a way that
is impossible to miss. This is a differentiation play, not a checkbox.

Google Search grounding is a first-party Gemini feature. Judges see
Google-native tooling used correctly. No RAG pipeline to build, no corpus to
maintain, no embeddings to evaluate. Ship the proof, mention RAG as a V2 roadmap
item.

---

## The Problem (Without This POC)

Without search grounding, the tutor has **all of these failure modes**:

| # | Failure | User impact | Root cause |
|---|---|---|---|
| 1 | Tutor fabricates a formula or date | Student learns wrong facts | LLM generates plausible but incorrect details from training data |
| 2 | Tutor confidently states something wrong | Student trusts the tutor and repeats the error | No verification step before teaching facts |
| 3 | Judges cannot see grounding happening | Lose points on "hallucination avoidance and grounding evidence" | Invisible internal reasoning, nothing shown in UI |
| 4 | Tutor stalls when unsure about a fact | Awkward silence, student loses confidence in tutor | No fallback mechanism when uncertain |
| 5 | Search triggers on coaching turns | Tutor pauses to "verify" when asking "What did you try first?" | No discrimination between factual queries and Socratic guidance |

**In the demo video, a wrong fact or a visible stall would undermine the entire submission.**

---

## What "Done" Looks Like

### Must-Have (POC ships to main app)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | `google_search` tool enabled in Gemini Live API config | Inspect config: `tools=[types.Tool(google_search=types.GoogleSearch())]` |
| **M2** | Grounding metadata parsed from Gemini responses | Backend logs show `grounding_citation` events with snippet + source |
| **M3** | Citation card renders in frontend (fade in, hold 8s, fade out) | Ask a factual question → toast appears bottom-right → auto-dismisses |
| **M4** | "Verifying..." indicator shows briefly during search | Visible purple spinner before citation card appears |
| **M5** | Tutor speaks grounded facts naturally (not robotic citations) | Tutor says "Let me check... yes, the formula is..." — never "According to search result 1..." |
| **M6** | Tutor skips search for coaching turns | Ask "Can you explain your thinking?" → no grounding event, no citation card |
| **M7** | Slow/empty search handled gracefully (no stalls) | Tutor continues coaching with a question if search is slow or empty |
| **M8** | Works in all 3 languages (PT/DE/EN) | Ask a fact in Portuguese or German → search adapts, citation appears |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Citations panel accumulates a searchable history of all grounding events | Right-side panel shows all citations with timestamps, newest first |
| S2 | Search query logged alongside citation | Backend logs and citation card show the query that triggered search |
| S3 | Grounding metrics visible in dashboard | Metrics row shows Search Events, Citations Shown, Turns |

### Won't Do (out of scope for this POC)

- RAG with curriculum materials (V2 — requires corpus, chunking, embeddings)
- Multiple citation sources per turn (one source is cleaner for the demo)
- Click-to-expand citation with full article excerpt
- Citation persistence across sessions (Firestore integration is a separate concern)

---

## Key Metrics

### Primary (must track in logs)

| Metric | Target | How measured |
|---|---|---|
| **Grounding event count** | >= 1 per factual question asked | Backend counter: `metrics["grounding_events"]` |
| **Citation render rate** | 100% of grounding events produce a visible card | Compare `grounding_events` (backend) to `citationsSent` (frontend) |
| **Search-vs-coaching discrimination** | 0 grounding events on pure coaching turns | Ask 5 coaching questions → `grounding_events` stays at 0 |
| **Hallucination rate** | 0 fabricated facts in grounded responses | Manual review: compare spoken fact to citation source |
| **Stall rate** | 0 silent gaps > 3s caused by search latency | Listen for pauses; check logs for gaps between audio chunks |

### Secondary (nice to see)

| Metric | Target | How measured |
|---|---|---|
| Search query relevance | Queries match student's question topic | Inspect `search_queries` list in backend final metrics |
| Toast display duration | 8s hold + 500ms fade | Visual inspection; JS timer in `showCitationToast()` |
| Citation card count per session | Matches grounding event count | Compare dashboard metrics to citations panel card count |

---

## Architecture Summary

```
┌────────────────────────────────────────────────────────────────────┐
│                          BROWSER                                   │
│                                                                    │
│  Mic ──→ PCM 16kHz ──→ WebSocket ──→ Server                      │
│  Camera ──→ JPEG frames ──→ WebSocket ──→ Server                  │
│                                                                    │
│  Speaker ←── AudioContext (24kHz) ←── audio chunks ←── WebSocket  │
│  Transcript panel ←── input/output transcripts ←── WebSocket      │
│                                                                    │
│  Citation toast (bottom-right)                                     │
│    ←── { type: "grounding", data: { snippet, source, url } }     │
│    ←── fade in 300ms, hold 8s, fade out 500ms                    │
│                                                                    │
│  Citations panel (right column, newest-first history)              │
│  Verifying... spinner (shown before citation arrives)              │
│  Metrics row: Search Events | Citations Shown | Turns | ...       │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                    FASTAPI (WebSocket bridge)                      │
│                                                                    │
│  Gemini Live API config:                                          │
│    model = gemini-2.0-flash-live-preview-04-09                    │
│    tools = [Tool(google_search=GoogleSearch())]                   │
│    response_modalities = ["AUDIO"]                                │
│    system_instruction includes grounding rules                    │
│                                                                    │
│  On every Gemini response message:                                │
│    1. _extract_grounding(msg) → parse grounding_metadata          │
│    2. Extract grounding_chunks[0].web.title + uri                 │
│    3. Send { type: "grounding", data: citation } to browser       │
│    4. Forward audio + transcripts as usual                        │
│                                                                    │
│  Also check grounding at turn_complete boundary                   │
│  Log all grounding events to JSONL session log                    │
│  Track: grounding_events, citations_sent, search_queries          │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                    GEMINI LIVE API                                  │
│                                                                    │
│  google_search tool enabled (built-in, not a function call)       │
│  Model decides when to search based on system prompt rules        │
│  Returns grounding_metadata with grounding_chunks + queries       │
│  Audio response includes naturally-worded verified facts          │
└────────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

1. **Built-in google_search, not a function-call tool** — The model handles search internally, returns grounding metadata alongside the audio response. No round-trip tool-call latency.
2. **System prompt discrimination** — The grounding rules in the system prompt tell the model when to search (facts) vs when to skip search (coaching). The model decides, not the backend.
3. **Single top citation** — Only the first grounding chunk is sent to the frontend. One clean source beats a wall of links.
4. **Dual grounding check** — Metadata is checked both on every streaming message and at the turn-complete boundary, because the Gemini API may attach grounding metadata at different points.

---

## What Ships to Main App

Once validated, these specific changes merge into the main app:

### Backend (`main.py` / `gemini_live.py`)
- `tools=[types.Tool(google_search=types.GoogleSearch())]` in Gemini config
- `_extract_grounding()` function for parsing grounding metadata
- Grounding rules appended to system prompt
- WebSocket message `{ type: "grounding", data: { snippet, source, url } }`
- Grounding metrics in session logs

### Frontend (`index.html`)
- Citation toast component (bottom-right overlay, 8s auto-dismiss)
- "Verifying..." spinner indicator
- `handleGrounding()` message handler
- Transcript entry for grounding events (purple "Search:" label)

### NOT shipped (POC-only)
- Citations panel (right-side history list) — debug/demo tool
- Grounding metrics dashboard row — debug tool
- JSONL file logging — debug tool

---

## Test Plan (Ordered by Priority)

Run each scenario at least **twice** to confirm consistency.

| # | Scenario | Pass criteria | Tests M# |
|---|---|---|---|
| 1 | **Factual question (EN)** — Ask "What's the formula for the area of a circle?" | Citation card appears with relevant source, tutor speaks the fact naturally | M1, M2, M3, M5 |
| 2 | **Coaching question (no search)** — Ask "Can you explain your thinking?" or "Show me your work" | No grounding event, no citation card, tutor coaches directly | M6 |
| 3 | **Citation toast lifecycle** — Trigger a grounding event, observe the toast | Toast fades in (300ms), holds (~8s), fades out (500ms) | M3 |
| 4 | **Verifying indicator** — Watch status area during a factual query | Purple "Verifying..." spinner appears briefly before citation | M4 |
| 5 | **Multilingual — Portuguese** — Ask "Qual e a formula da area de um circulo?" | Search triggers, citation appears, tutor responds in Portuguese | M8 |
| 6 | **Multilingual — German** — Ask "Was ist die Hauptstadt von Deutschland?" | Search triggers, citation appears, tutor responds in German | M8 |
| 7 | **Natural speech check** — Listen to 3 grounded responses | Tutor never says "According to my search results..." or reads citations robotically | M5 |
| 8 | **Graceful failure** — Ask an obscure or nonsensical factual question | Tutor says something like "I'm not fully sure — let's reason through it together" instead of stalling | M7 |
| 9 | **Mixed turn sequence** — Alternate: factual question → coaching question → factual question | Grounding fires only on factual turns, coaching turns are search-free | M6 |
| 10 | **Demo script end-to-end** — Run the full demo script from rules.md 3 times | All steps pass cleanly each run | M1-M8 |

---

## Timeline

This POC is part of **Week 2-3** work (Feb 24 – Mar 9). The code is already
drafted. Validation involves running the test plan above and confirming all
Must-Have criteria pass. Integration into the main app should happen after
POC validation, before the demo video is recorded (Week 4).
