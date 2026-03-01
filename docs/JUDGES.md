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
