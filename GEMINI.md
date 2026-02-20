# SeeMe Tutor Context

## Project Snapshot

SeeMe Tutor is a real-time multimodal tutoring app for the Gemini Live Agent Challenge.

Core promise: the tutor sees homework, hears questions, and guides with Socratic pedagogy in PT/EN/DE.

### Demo-Critical Moments

1. Proactive visual observation (without user prompt).
2. Immediate interruption handling (true barge-in).
3. Multilingual pedagogy (explain in one language, practice in another).

If these are not stable, all other work is secondary.

---

## Current Stack

- Frontend: single-file PWA (`frontend/index.html`) on Firebase Hosting.
- Backend: FastAPI websocket bridge (`backend/main.py`) on Cloud Run.
- AI: Gemini Live API via `google-genai` (`backend/gemini_live.py`).
- Data: Firestore session metadata/progress.
- Secrets: Secret Manager (`GEMINI_API_KEY`).

---

## Canonical Planning Docs

- Product requirements: `SeeMeTutor_PRD.md`
- Prioritized backlog: `epics_todo.md`
- Schedule and gates: `TIMELINE.md`
- Optional backlog: `extra_miles.md`
- Public-facing project overview: `README.md`

Keep these files consistent when changing scope, priorities, or delivery dates.

---

## Delivery Rules

- Prioritize judge-visible reliability over feature breadth.
- Do not expand scope before P0 demo moments are stable.
- Favor low-latency and clear UX over extra infrastructure complexity.
- Preserve educational safety and privacy-by-design language in docs and prompts.

---

## Local Run Reminder

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
GEMINI_API_KEY=your_key_here uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000` and allow mic/camera.
