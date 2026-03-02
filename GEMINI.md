# SeeMe Tutor - Context & Instructions

## Project Overview
SeeMe Tutor is a real-time multimodal AI study companion built on the **Gemini 2.5 Flash Live API**. It uses audio and video (camera) streams to provide Socratic tutoring, helping students discover answers rather than giving them directly.

**Key Capabilities:**
- **Real-time Multimodal:** Processes audio and video (via WebSocket) to "see" homework and "hear" questions.
- **Socratic Pedagogy:** Guided by LearnLM principles; asks questions, provides hints, and verifies mastery (Solve -> Explain -> Transfer).
- **Multilingual:** Auto-detects and speaks Portuguese, German, French, and English.
- **Proactive Vision:** Actively watches the camera feed to comment on work without being asked.

## Architecture & Tech Stack

### Backend (`backend/`)
- **Framework:** Python 3.12+ with **FastAPI** (`main.py`).
- **AI Core:** **Google Agent Development Kit (ADK)** (`google-adk`) managing **Gemini 2.5 Flash Live** sessions.
- **Streaming:** WebSockets for bidirectional audio/video streaming (`modules/ws_bridge.py`).
- **Database:** **Google Firestore** for session state, student profiles, and progress tracking.
- **Infrastructure:** Google Cloud Run (Serverless Container).

### Frontend (`frontend/`)
- **Type:** Progressive Web App (PWA).
- **Tech:** Vanilla HTML5, CSS, JavaScript.
- **Streaming:** Uses Web Audio API (`ScriptProcessorNode`/`AudioContext`) for PCM audio and Canvas for JPEG frame capture.
- **Hosting:** Firebase Hosting.

### Infrastructure
- **Deployment:** `deploy.sh` handles building containers (Cloud Build) and deploying to Cloud Run & Firebase.
- **Secrets:** Google Secret Manager (e.g., `DEMO_ACCESS_CODE`).

## Key Files & Directories

- `backend/main.py`: Entry point. Initializes FastAPI, WebSocket endpoint, ADK Runner, and session management.
- `backend/agent.py`: Defines the ADK Agent, tools (`write_notes`, `google_search_agent`, `verify_mastery_step`), and the System Prompt (Phases: Greeting, Capture, Tutoring, Review).
- `backend/MIGRATION.md`: **CRITICAL**. Tracks the active migration to ADK. Check this for the latest implementation status of features (PoCs).
- `backend/modules/`: Modular logic for specific features:
  - `proactive.py`: Proactive vision logic.
  - `whiteboard.py`: Note management and deduplication.
  - `guardrails.py`: Safety checks for input/output.
  - `grounding.py`: Search citation extraction.
- `frontend/index.html`: Main frontend client.

## Development Workflow

### 1. Backend Setup
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Local Development
Run the backend locally. It serves the frontend static files at `http://localhost:8000`.
```bash
# In backend/
uvicorn main:app --reload --port 8000
```
*Note: Requires Google Cloud authentication (`gcloud auth application-default login`).*

### 3. Testing
Uses `pytest` for the backend.
```bash
# In backend/
pytest
```

### 4. Deployment
Use the provided script. **Do not deploy manually.**
```bash
./deploy.sh
```

## Core Concepts & Conventions

### Session Phases (`backend/agent.py`)
The agent operates in distinct phases. Transitions are explicit via the `set_session_phase` tool.
1.  **Greeting:** Welcome, context loading, goal setting.
2.  **Capture:** Scanning homework via camera. No teaching yet.
3.  **Tutoring:** Core Socratic loop (Hint -> Suggest -> Verify).
4.  **Review:** Summary and celebration.

### Tooling
- **`write_notes`**: Displays content (formulas, checklists) on the user's whiteboard.
- **`verify_mastery_step`**: Enforces the 3-step mastery protocol (Solve, Explain, Transfer).
- **`google_search_agent`**: Used *only* for educational grounding when explicitly requested.

### Coding Style
- **Python:** Type-hinted, modular (`modules/`), using `asyncio` extensively.
- **Logging:** Structured logging is essential for debugging the real-time stream (`debug.log`).
- **Safety:** Strict guardrails against non-educational drift, prompt injection, and giving direct answers.

## Current Status (as of 2026-03-02)
The project is finalizing the **ADK Integration** (`dev_integration` branch).
- **Done:** ADK Skeleton, Proactive Vision, Screen Share, Whiteboard Sync, Safety/Grounding.
- **In Progress/Next:** Latency optimization, Frontend unification.
- **Reference:** See `backend/MIGRATION.md` for the detailed checklist.
