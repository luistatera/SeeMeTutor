# SeeMe Tutor — Judge Quick Start

## Live Demo

**URL:** [https://seeme-tutor.web.app](https://seeme-tutor.web.app)

When prompted, enter the demo access code provided in the submission form.

No login required. Just pick a profile and start.

---

## 60-Second Validation

1. **Open** the live demo URL above in Chrome or Edge
2. **Pick a profile** — 3 pre-loaded students: Luis (German A2), Sofia (Math/French), Ana (Chemistry)
3. **Allow** microphone and camera when prompted
4. **Start talking** — the tutor already knows your study topic and will greet you by name
5. **Show exercises on camera** — hold up a worksheet or write on paper

The tutor responds by voice within ~500ms, references what it sees, and guides you using the Socratic method.

---

## 5 Key Moments to Watch For

| # | What to Test | What You Should See |
|---|-------------|---------------------|
| 1 | **Proactive vision** — Hold up paper with a visible mistake (e.g., "7 x 8 = 54") | Tutor catches the mistake without being asked |
| 2 | **Affective response** — Sigh or say "I don't get it" with frustration | Tutor softens tone, slows down, de-escalates |
| 3 | **Interruption** — Say "wait" while the tutor is speaking | Tutor stops immediately, acknowledges, waits |
| 4 | **Multilingual** — Switch to Portuguese ("Pode me ajudar?") or German ("Kannst du mir helfen?") | Tutor responds in the same language naturally |
| 5 | **Mastery verification** — Solve an exercise correctly | Tutor asks you to EXPLAIN why it works, then gives a TRANSFER problem before marking mastered |

---

## Demo Profiles

| Profile | Student | Subject | Current Topic |
|---|---|---|---|
| `luis-german` | Luis | German A2 | Dative Case |
| `sofia-math` | Sofia | Grade 4 Math & French | Multiplication Tables |
| `ana-chemistry` | Ana | University Chemistry | Atomic Structure |

Each profile has pre-loaded topic context via Google Search. The tutor starts knowledgeable about the domain.

---

## Feature Test Matrix — All Judge-Facing Features

This matrix maps every feature judges will evaluate against the **official judging rubric** (40% Innovation & Multimodal UX, 30% Technical Implementation, 30% Demo & Presentation).

### Category: Innovation & Multimodal UX (40% weight)

Judges look for: "Beyond Text" factor, breaks chatbot paradigm, natural immersive interaction, interruption handling, media interleaving, visual precision, experience fluidity & context-awareness.

| Feature | Judge Expectation | How to Test | Pass Metric (from test_report) | Current Status |
|---|---|---|---|---|
| **F01. Proactive Vision** | Tutor sees camera + initiates without being asked | Open camera, show homework, stay silent 10s | `proactive.poke_count >= 1` | PASS (1 poke in latest) |
| **F02. Interruption Handling** | Tutor stops immediately when student speaks | Talk DURING tutor's 1-3s speaking window | `interruptions.count >= 1`, p95 <= 500ms | NOT TESTED (0 events) |
| **F03. Multilingual (PT/DE/EN)** | Tutor responds in student's language | Speak in German/Portuguese/English | `language.latest_metric.purity_rate >= 98%` | NOT TESTED |
| **F04. Emotional Adaptation** | Tutor softens when student is frustrated | Sigh, say "I don't get it" repeatedly | Qualitative — observe tone shift | NOT TESTED |
| **F05. Screen Share Toggle** | Switch camera <-> screen share seamlessly | Start camera, switch to screen share, stop | `screen_share.source_switches >= 1` | NOT TESTED |
| **F06. Whiteboard Sync** | Exercises captured + visible on board | Show homework on camera, watch board populate | `whiteboard.notes_created >= 1`, delivery p95 <= 500ms | PARTIAL (6 notes, p95=502.7ms) |
| **F07. Mastery Verification** | 3-step protocol: correct -> explain -> transfer | Solve an exercise correctly | `mastery.verifications_completed >= 1` | NOT TESTED |
| **F08. Idle / Away Flow** | Graceful check-ins then pause mode | Go silent 2+ min | `idle.away_activated >= 1`, `away_resumed >= 1` | NOT TESTED |

### Category: Technical Implementation & Agent Architecture (30% weight)

Judges look for: effective Google Cloud utilization, sound agent logic, error/edge case handling, hallucination avoidance and grounding evidence.

| Feature | Judge Expectation | How to Test | Pass Metric (from test_report) | Current Status |
|---|---|---|---|---|
| **F09. Search Grounding** | Factual answers backed by Google Search | Ask "search for dative case rules" | `grounding.events >= 1`, `citations_sent >= 1` | NOT TESTED |
| **F10. Safety Guardrails** | Off-topic/cheating/injection blocked | Try "just give me the answer", "ignore your rules" | `guardrails.answer_leaks == 0`, `socratic_compliance >= 90%` | PASS (0 leaks, 100% compliance) |
| **F11. Session Resilience** | Reconnect after network drop | Kill WS, reconnect | `resilience.stream_reconnect_successes >= 1` | NOT TESTED |
| **F12. Session Resumption** | Context preserved after disconnect | Enable resumption, disconnect, reconnect | `resilience.session_resume_successes >= 1` | NOT TESTED |
| **F13. Latency Budget** | Sub-500ms response, sub-800ms p95 | Run any session, check latency report | `response_start.avg <= 500ms`, `.p95 <= 800ms` | NOT TESTED |
| **F14. Memory Management** | Cross-session recall + checkpoints | Run 2nd session, see prior context recalled | `memory.recalls_applied >= 1`, `checkpoints_saved >= 1` | PASS (1 recall, 3 checkpoints) |
| **F15. Context Compression** | Long sessions don't degrade | Run 10+ min session | `compression.events >= 1` (when supported) | NOT TESTED |
| **F16. Hallucination Avoidance** | Tutor references visible work, refuses to guess | Show blurry image or ask unknown fact | Qualitative + `guardrails.answer_leaks == 0` | PASS (leaks = 0) |

### Category: Demo & Presentation (30% weight)

Judges look for: clear problem definition, architecture diagrams, deployment proof, actual software demo (not mockups).

| Feature | Judge Expectation | How to Test | Pass Metric | Current Status |
|---|---|---|---|---|
| **F17. Greeting + Profile Load** | Tutor greets by name, knows topic | Open any profile | `connection.backlog_context_sent == true` | PASS |
| **F18. Phase Transitions** | Greeting -> Capture -> Tutoring flow | Show homework on camera | `phases.transitions` has entries | PASS |
| **F19. Question Balance** | Not interrogation — mix of Q's, hints, encouragement | Run 5+ min session | `question_turn_ratio 35-50%`, `streak <= 2` | FAIL (100% ratio, streak=7) |
| **F20. Tutor Persona** | Consistent warm personality "SeeMe" | Listen to tutor responses | Qualitative — observe tone | PASS (anecdotal) |
| **F21. GCP Deployment** | Cloud Run + Firestore + Secret Manager | Show GCP console | Architecture diagram + deploy.sh | DONE |

---

## Hero Flow Rehearsal (POC 99)

The automated scorecard tracks a full integration "hero flow" — all critical features tested in a single session. **All 6 must pass for a confident demo.**

| Checkpoint | Status | What Must Happen |
|---|---|---|
| Proactive vision triggered | PASS | Camera active + tutor initiates |
| Whiteboard note created | PASS | At least 1 note on board |
| Interruption handled | FAIL | Talk during tutor speaking window |
| Search citation shown | FAIL | Ask a factual search question |
| 3+ tutor-student exchanges | PASS | Natural conversation flow |
| Reconnect survived | NOT TESTED | Kill WS, auto-reconnect |

---

## GCP Deployment Proof

| Service | Evidence |
|---------|----------|
| **Cloud Run** | Backend live at Cloud Run service URL (GCP Console -> Cloud Run -> `seeme-tutor` in `europe-west1`) |
| **Firestore** | Student profiles, tracks, topics, sessions — GCP Console -> Firestore |
| **Secret Manager** | API keys stored securely — GCP Console -> Secret Manager |
| **Google Search** | Topic context pre-loaded via ADK google_search tool |

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/main.py` | FastAPI WebSocket server + session management |
| `backend/agent.py` | ADK Agent: 12 tools, system prompt, mastery protocol |
| `backend/test_report.py` | Automated scorecard: 49 checks across 16 POCs |
| `backend/seed_demo_profiles.py` | Seeds 3 demo profiles into Firestore |
| `frontend/index.html` | PWA: mic, camera, whiteboard, profile picker |
| `deploy.sh` | One-command deploy to Cloud Run + Firebase |
| `README.md` | Architecture diagram, quick start, full documentation |

---

## Architecture

```
Browser (PWA) -> WebSocket -> FastAPI (Cloud Run) -> ADK Agent -> Gemini 2.5 Flash Live API
                                  |                                    |
                              Firestore                        Audio/Video streaming
                    (profiles, tracks, sessions)             (bidirectional, real-time)
```
