# SeeMe Tutor Context

## Project Overview

SeeMe Tutor is a real-time multimodal AI tutoring application built for the Gemini Live Agent Challenge. It leverages the **Gemini 2.0 Flash Live API** to see a student's homework through the camera, hear their questions, and guide them using the **Socratic method** in their preferred language (English, Portuguese, German).

### Key Features
*   **Live Vision:** Processes continuous camera feed to understand handwritten work, diagrams, and text.
*   **Natural Voice:** Full-duplex audio allows for natural interruptions and conversation.
*   **Socratic Teaching:** Guides students to answers rather than providing them directly.
*   **Multilingual:** seamlessly switches between languages based on user speech.
*   **Emotional Adaptation:** Detects frustration or confidence and adjusts tone/pace.

### Architecture
*   **Frontend:** Progressive Web App (PWA) using plain HTML/CSS/JS. Handles WebRTC (camera), AudioContext (microphone/playback), and WebSocket communication. Hosted on **Firebase Hosting**.
*   **Backend:** **FastAPI** (Python) service on **Google Cloud Run**. Manages WebSocket connections and bridges data to the Gemini Live API.
*   **AI:** **Gemini 2.0 Flash Live API** via `google-genai` SDK.
*   **State:** **Firestore** for session persistence.
*   **Secrets:** **Secret Manager** for API keys in production.

## Building and Running

### Prerequisites
*   Python 3.12+
*   Gemini API Key (from Google AI Studio)
*   `gcloud` CLI (for deployment)

### Local Development
1.  **Configure Environment:**
    *   Copy `.env.example` to `.env` in the root or `backend/` directory.
    *   Set `GEMINI_API_KEY=your_key_here`.

2.  **Run Backend:**
    ```bash
    cd backend
    python3 -m venv .venv
    source .venv/bin/activate  # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    uvicorn main:app --reload --port 8000
    ```

3.  **Access Frontend:**
    *   Open `http://localhost:8000` in a modern browser (Chrome/Edge).
    *   Allow microphone and camera permissions.

### Deployment
The project includes a unified deployment script for Google Cloud Platform:
```bash
./deploy.sh
```
This script:
1.  Builds the backend Docker image via Cloud Build.
2.  Deploys the container to Cloud Run.
3.  Deploys the frontend to Firebase Hosting.

## Development Conventions

*   **Code Style:**
    *   **Python:** Follows PEP 8. formatted with `black` or similar is recommended.
    *   **Frontend:** Standard HTML5/CSS3/ES6+. No build step (e.g., Webpack/Vite) is currently used; files are served statically.
*   **State Management:** Frontend uses vanilla JS state. Backend is stateless except for the active WebSocket/Gemini session.
*   **Logging:** Python standard `logging` module is used.
*   **Dependencies:** Managed via `backend/requirements.txt`.

## Key Files

*   `backend/main.py`: Main FastAPI application, WebSocket endpoint (`/ws`), and static file serving.
*   `backend/gemini_live.py`: Manages the session with Gemini Live API, handling audio/video stream bridging.
*   `frontend/index.html`: The complete frontend application (UI, logic, WebRTC, AudioContext).
*   `infrastructure/gcp_services.py`: demonstrative script showing GCP service integrations.
*   `SeeMeTutor_PRD.md`: Detailed Product Requirements Document explaining the "why" and "how" of the project.
