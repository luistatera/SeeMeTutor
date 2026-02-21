# SeeMe Tutor — Judge Quick Start

## Live Demo

**URL:** [https://seeme-tutor.web.app](https://seeme-tutor.web.app)

When prompted, enter the demo access code provided in the submission form.

---

## 60-Second Validation

1. **Open** the live demo URL above in Chrome or Edge
2. **Allow** microphone and camera when the browser prompts (a consent screen will appear first)
3. **Start talking** — say something like "Can you help me with this math problem?" while holding a worksheet or notebook up to the camera

The tutor will respond by voice within ~500ms. You should hear a natural, conversational response guiding you through the problem.

---

## 3 Proof Moments to Watch For

| # | What to Test | What You Should See |
|---|-------------|---------------------|
| 1 | **Proactive observation** — Hold up a piece of paper with a visible math mistake (e.g., "7 x 8 = 54") | The tutor notices the mistake without being asked and guides you to correct it |
| 2 | **Interruption handling** — Say "wait" or "hold on" while the tutor is speaking | The tutor stops immediately, acknowledges you, and waits |
| 3 | **Multilingual** — Switch to Portuguese or German mid-sentence (e.g., "Pode me ajudar?" or "Kannst du mir helfen?") | The tutor responds in the same language without any configuration |

---

## GCP Deployment Proof

| Service | Evidence |
|---------|----------|
| **Cloud Run** | Backend is live at the Cloud Run service URL (visible in GCP Console → Cloud Run → `seeme-tutor` in `europe-west1`) |
| **Firestore** | Session metadata logged automatically — see GCP Console → Firestore → `sessions` collection |
| **Secret Manager** | API keys stored securely — see GCP Console → Secret Manager → `gemini-api-key`, `demo-access-code` |
| **GCP Services Proof Script** | [`infrastructure/gcp_services.py`](infrastructure/gcp_services.py) — run `python infrastructure/gcp_services.py` to verify all services |

---

## Key Files

| File | Purpose |
|------|---------|
| [`backend/main.py`](backend/main.py) | FastAPI WebSocket server — bridges browser to Gemini Live API |
| [`backend/gemini_live.py`](backend/gemini_live.py) | Gemini Live API session management via ADK |
| [`frontend/index.html`](frontend/index.html) | Single-file PWA — mic, camera, audio playback |
| [`deploy.sh`](deploy.sh) | Automated one-command deploy to Cloud Run + Firebase Hosting |
| [`infrastructure/gcp_services.py`](infrastructure/gcp_services.py) | GCP services proof script |
| [`README.md`](README.md) | Full documentation, architecture diagram, rubric-to-evidence table |

---

## Architecture Diagram

See the [Mermaid diagram in README.md](README.md#architecture) for the full data flow:

```
Browser (PWA) → WebSocket → FastAPI (Cloud Run) → Gemini 2.5 Flash Live API
                                ↕                         ↕
                           Firestore              Audio/Video streaming
                        (session state)          (bidirectional, real-time)
```

---

## Automated Deployment

The [`deploy.sh`](deploy.sh) script performs a full automated deployment:

```bash
./deploy.sh
```

This builds the container via Cloud Build, deploys to Cloud Run with Secret Manager bindings, and publishes the frontend to Firebase Hosting — all in one command.
