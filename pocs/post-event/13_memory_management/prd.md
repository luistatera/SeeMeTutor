# POC 13 — Memory Management & Long-Horizon Recall: Mini PRD

## Why This Matters

SeeMe Tutor currently performs well inside a single live session, but long-term
learning requires continuity across sessions. If the tutor cannot reliably
remember prior goals, struggles, and wins, each class feels disconnected and
students lose trust in personalization.

This POC adds a structured memory layer that converts session artifacts into
compact, reusable memory objects. The tutor can then retrieve only relevant
memories at session start and topic transitions, improving coaching quality
without exploding context windows.

---

## The Problem (Without This POC)

| # | Broken behavior | User impact | Root cause in main app |
|---|---|---|---|
| 1 | Session amnesia | Tutor repeats introductions and misses prior struggles | No cross-session memory retrieval |
| 2 | Generic coaching | Advice is broad, not personalized to learner history | No typed memory model (facts/preferences/risks) |
| 3 | Context bloat risk | Attempting to replay whole transcripts can cause instability | No memory compression or salience filtering |
| 4 | Weak longitudinal proof | Hard to show learning progression over multiple sessions | Insights stored per session but not synthesized |
| 5 | Fragile handoffs | Reconnect/resume can restore short state but not long-term intent | Memory not persisted as reusable recall units |

---

## What "Done" Looks Like

### The Memory Flow (Ordered)

1. **Ingestion Trigger**
   - At session completion, ingest structured artifacts from POC 08
     (`session_summaries`) and POC 12 (`StudentReport`) plus onboarding profile
     context from POC 00.
2. **Memory Cell Extraction**
   - Convert data into typed memory cells:
     `fact`, `plan`, `preference`, `decision`, `task`, `risk`.
   - Each cell includes `topic/scene`, `salience`, `source_session_id`,
     `created_at`.
3. **Scene Consolidation**
   - Build/refresh concise scene summaries (stable < 120 words each) used for
     retrieval-time context compression.
4. **Recall Injection**
   - Before a new session starts (or when topic shifts), retrieve top relevant
     cells + scene summaries and inject as hidden context.
5. **Safety + Explainability**
   - Every recalled item must include source trace metadata.
   - Recalled memory cannot override current-session evidence if conflicting.

### Must-Have (POC ships to main app)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | Memory cells persisted per student in Firestore | New `student_memory` docs created after session end |
| **M2** | Typed schema enforcement | Invalid cell type/shape rejected by validator |
| **M3** | Scene summaries generated deterministically | `memory_scenes` updated for touched topics |
| **M4** | Relevant recall at next session start | Tutor references prior struggle/goal within first 90s |
| **M5** | Retrieval uses salience + relevance filtering | Logs show ranking inputs and selected top-k |
| **M6** | Context budget guardrail | Injected memory payload always below configured token cap |
| **M7** | Conflict handling | If memory conflicts with present evidence, tutor asks to confirm |
| **M8** | Traceability | Every recalled item is linked to source session/report IDs |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Memory decay/refresh policy | Low-salience stale memories decay over time |
| S2 | Parent dashboard memory view | Timeline of key memories visible in UI |
| S3 | Topic-level recall controls | Toggle strict recall per subject/topic |

### Won't Do (out of scope for this POC)

- Full vector database migration (keep Firestore-first for now)
- Cross-student global memory sharing
- Autonomous curriculum planning across months
- Complex RL-based memory scoring

---

## Key Metrics

### Primary (must track in logs)

| Metric | Target | How measured |
|---|---|---|
| **Recall precision@k** | >= 0.80 | Manual scoring of top-k recalls against session objective |
| **Cold-start personalization** | >= 90% | Sessions where tutor references valid prior context in first 90s |
| **Memory injection budget compliance** | 100% | No turn exceeds memory token budget cap |
| **Conflicting memory resolution rate** | 100% | Conflicts lead to confirmatory prompt, not blind assertion |
| **Data linkage completeness** | 100% | Recalled memories contain valid source IDs |

### Secondary (nice to see)

| Metric | Target | How measured |
|---|---|---|
| Memory update latency | < 5s P90 after session close | End-of-session trigger to DB write completion |
| Scene summary stability | >= 90% | Repeated consolidations keep core facts consistent |
| Human usefulness score | >= 4/5 | Teacher/parent rating of recall relevance |

---

## Architecture Summary

```
┌────────────────────────────────────────────────────────────────────┐
│                    SESSION ARTIFACT SOURCES                        │
│  POC 00: students/ profile  |  POC 08: session_summaries          │
│  POC 12: final reports      |  POC 06: reconnect session context   │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│            MEMORY ORCHESTRATOR (FastAPI background task)           │
│  1) Extract typed cells  2) Score salience  3) Upsert scenes       │
│  4) Store trace metadata 5) Enforce schema + token budget          │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                     FIRESTORE MEMORY LAYER                          │
│  student_memory/{student_id}/cells/{cell_id}                        │
│  student_memory/{student_id}/scenes/{scene_id}                      │
│  memory_events/{session_id} (debug + metrics)                       │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│                LIVE SESSION START / TOPIC SWITCH                    │
│  retrieve_relevant_memory(query, student_id, topic)                 │
│  -> inject hidden memory context -> tutor responds with continuity  │
└────────────────────────────────────────────────────────────────────┘
```

**Key design decisions:**

1. **Typed memories over raw transcript replay** to reduce context bloat.
2. **Scene summaries + top-k cells** for deterministic, bounded recall.
3. **Source-trace metadata** for explainability and debugging.
4. **Conflict-first behavior** (ask to confirm, do not assert stale memory).

---

## What Ships to Main App

### Backend (`main.py`, `tutor_agent/reflection_agent.py`, new memory module)

- End-of-session memory ingestion hook after POC 08 summary completion
- `memory_manager.py` (cell extraction, salience, consolidation)
- `memory_store.py` (Firestore reads/writes for cells/scenes)
- Retrieval hook before session start and on topic transition
- Metrics logging for recall quality and budget compliance

### Frontend (`index.html` / dashboard)

- No mandatory UI changes for v1 recall
- Optional debug badge: "Memory recall active" + recalled topic count

### NOT shipped (POC-only)

- Memory inspector/debug table with full cell contents
- Advanced manual memory edit tools

---

## Test Plan (Ordered by Priority)

| # | Scenario | Pass criteria | Tests M# |
|---|---|---|---|
| 1 | **Post-session ingestion** — End normal 5+ turn class | Memory cells + scene summary created for student | M1, M3 |
| 2 | **Next-session recall** — Rejoin same student next day | Tutor references prior struggle/goal correctly early | M4, M8 |
| 3 | **Topic shift recall** — Mid-session switch math -> German | Relevant recall changes to new topic scene | M5 |
| 4 | **Budget stress** — Student with 30+ prior sessions | Retrieval remains within token cap; no overflow behavior | M6 |
| 5 | **Conflict test** — Prior memory says "prefers PT", student asks EN | Tutor asks to confirm and adapts, no stubborn stale recall | M7 |
| 6 | **Traceability audit** — Inspect recalled items in logs | Every recalled item has source IDs and timestamps | M8 |
| 7 | **Bad schema input** — Inject invalid cell type via test fixture | Validator rejects write, session continues safely | M2 |

---

## Connections to Other POCs

- **POC 00 (Onboarding):** Seeds baseline identity/goals used for long-term memory keys.
- **POC 08 (A2A Summary):** Primary source of structured per-session insights.
- **POC 12 (Final Student Report):** Provides deterministic artifacts for memory ingestion.
- **POC 06 (Session Resilience):** Complements short-term reconnect state with long-term recall.
- **POC 09 (Safety Guardrails):** Ensures recalled memory is used as guidance, not rigid truth.
- **POC 99 (Hero Rehearsal):** Optional future rehearsal extension to demonstrate multi-session continuity.

---

## Timeline

This POC is intentionally positioned as **Post-Hackathon / Phase 2** work,
after the core demo-critical flow is stable.

Recommended sequence:
1. Complete and stabilize POCs 00-12 plus 99.
2. Implement POC 13 backend memory pipeline first (no UI dependency).
3. Add minimal recall injection and validate with multi-session tests.
4. Optionally expose memory observability tools in dashboard after stability.
