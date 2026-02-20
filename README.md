# SeeMe Tutor

**The AI tutor that sees your homework, hears your confusion, and speaks your language.**

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Firebase%20Hosting-orange?style=flat-square)](https://seeme-tutor.web.app)
[![Cloud Run](https://img.shields.io/badge/Backend-Cloud%20Run-blue?style=flat-square)](https://console.cloud.google.com/run)
[![Gemini](https://img.shields.io/badge/AI-Gemini%202.5%20Flash%20Live-green?style=flat-square)](https://ai.google.dev/)

---

## What Is SeeMe Tutor?

SeeMe Tutor is a real-time multimodal AI tutoring application built on the **Gemini 2.5 Flash Live API**. It sees your homework through the camera, hears your questions through the microphone, and guides you — in your own language — using the Socratic method. It never just gives you the answer.

**Key differentiators:**

- **Proactive Visual Co-pilot** — Doesn't wait to be asked. Actively watches through the camera and comments on mistakes, completed steps, and progress in real time
- **Live Vision** — Continuous camera feed lets the tutor see what you're working on in real time, not just a snapshot
- **Natural Voice** — Full-duplex audio with sub-500ms response latency; interrupt naturally and the tutor adapts immediately
- **Socratic Method** — Guides students to discover answers themselves; never gives the solution directly
- **Multilingual** — Auto-detects Portuguese, German, and English; switches mid-session without configuration
- **Emotional Adaptation** — Detects frustration or confidence in the student's voice and adjusts pace and tone accordingly
- **Silence Handling** — Checks in after pauses ("Still working on it? Take your time") without being pushy
- **Privacy by Design** — Consent screen before session start, anonymized session data, clear camera-active indicators, and transparent data handling
- **Proactive Tool Calling** — Uses live tool execution to fetch definitions and formulas without breaking the student's flow

---

## Judge Quick Validation (What Must Be Obvious in 4 Minutes)

These are the three non-negotiable proof moments for the hackathon demo:

1. **Proactive observation:** Tutor catches a visible mistake without being asked.
2. **Interruption handling:** Student says "wait" mid-response and tutor stops immediately.
3. **Multilingual pedagogy:** Tutor explains in one language and practices in another.

If any of these are missing, scoring potential drops significantly even if the stack is technically strong.

---

## Current Documentation

- Product requirements: `SeeMeTutor_PRD.md`
- Execution backlog: `epics_todo.md`
- Delivery timeline: `TIMELINE.md`
- Low-priority backlog: `extra_miles.md`

---

## Architecture

```mermaid
flowchart TD
    subgraph Browser["Browser — PWA (Firebase Hosting)"]
        MIC["Microphone\nPCM 16kHz via ScriptProcessorNode"]
        CAM["Camera\nJPEG frames via Canvas (0.5-3fps adaptive)"]
        PLAY["Audio Playback\nPCM 24kHz via AudioContext"]
    end

    subgraph Backend["FastAPI Backend (Cloud Run — europe-west1)"]
        WS["WebSocket Handler\nmain.py"]
        GL["Gemini Live Session Manager\n(Built with google-adk)"]
    end

    subgraph Google["Google AI"]
        GEMINI["Gemini 2.5 Flash Live API\ngemini-2.5-flash-native-audio-preview-12-2025"]
    end

    subgraph GCP["GCP Supporting Services"]
        FS["Firestore\nSession State"]
        SM["Secret Manager\nAPI Keys"]
    end

    MIC -- "WebSocket\naudio: base64 PCM 16kHz" --> WS
    CAM -- "WebSocket\nvideo: base64 JPEG" --> WS
    WS -- "Blob: audio/pcm;rate=16000" --> GL
    WS -- "Blob: image/jpeg" --> GL
    GL -- "Gemini Live streaming session" --> GEMINI
    GEMINI -- "Audio response\nPCM 24kHz" --> GL
    GL -- "WebSocket\naudio: base64 PCM 24kHz" --> PLAY
    WS <--> FS
    SM -- "GEMINI_API_KEY" --> WS
```

---

## Quick Start

Get SeeMe Tutor running locally in three steps.

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/seeme-tutor.git
cd seeme-tutor

# 2. Configure your API key
cp .env.example .env
# Open .env and set: GEMINI_API_KEY=your_key_here

# 3. Install dependencies and run
cd backend
pip install -r requirements.txt
GEMINI_API_KEY=your_key uvicorn main:app --reload --port 8000
```

Open [http://localhost:8000](http://localhost:8000) in your browser. Allow microphone and camera access when prompted.

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Python | 3.12 or higher |
| Gemini API key | Free at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Modern browser | Chrome or Edge recommended (WebRTC + AudioContext support) |
| HTTPS or localhost | Camera/mic APIs require a secure context |

**For GCP deployment (optional):**

- `gcloud` CLI — [install guide](https://cloud.google.com/sdk/docs/install)
- `firebase` CLI — `npm install -g firebase-tools`
- A GCP project with billing enabled

---

## Full Setup Guide

### Get a Gemini API Key

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Click **Create API key**
3. Copy the key — you will use it in `.env` for local development and in Secret Manager for production

### Local Development

```bash
# Clone and enter the repo
git clone https://github.com/YOUR_USERNAME/seeme-tutor.git
cd seeme-tutor

# Create and activate a virtual environment
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set your API key and start the server
GEMINI_API_KEY=your_key_here uvicorn main:app --reload --port 8000
```

The FastAPI server serves the frontend at `http://localhost:8000` and exposes a WebSocket at `ws://localhost:8000/ws`.

**Mac users:** If you want to run local audio test scripts, install PortAudio first:

```bash
brew install portaudio
```

### GCP Deployment

Use the included deploy script for a one-command deployment to Cloud Run and Firebase Hosting:

```bash
chmod +x deploy.sh
./deploy.sh
```

The script handles everything listed in the [Deployment to GCP](#deployment-to-gcp) section below.

### GCP Services Setup

If you are deploying to your own GCP project, enable the required APIs and create a service account:

```bash
# Set your project
export PROJECT_ID=your-project-id
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com

# Create a service account
gcloud iam service-accounts create seeme-tutor-sa \
  --display-name="SeeMe Tutor Service Account"

# Grant required roles
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:seeme-tutor-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:seeme-tutor-sa@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

# Store the Gemini API key in Secret Manager
echo -n "your_gemini_api_key_here" | \
  gcloud secrets create gemini-api-key --data-file=-
```

---

## Project Structure

```
seeme-tutor/
├── backend/
│   ├── main.py           # FastAPI app + WebSocket endpoint
│   ├── gemini_live.py    # Gemini Live API session management
│   ├── requirements.txt  # Python dependencies
│   └── Dockerfile        # Container image for Cloud Run
├── frontend/
│   └── index.html        # PWA: mic capture, camera, audio playback
├── infrastructure/
│   └── gcp_services.py   # Demonstrates GCP service usage for judges
├── deploy.sh             # One-command deploy to Cloud Run + Firebase
├── .env.example          # Environment variable template
└── README.md
```

---

## How It Works — Technical Pipeline

SeeMe Tutor runs a real-time bidirectional pipeline between the browser and Gemini:

1. **Mic capture** — The browser uses a `ScriptProcessorNode` to capture raw PCM audio at 16kHz from the system microphone. Audio chunks are base64-encoded and sent to the backend over WebSocket as JSON.

2. **Camera capture** — The browser intelligently draws the current camera frame to an HTML5 canvas (dynamically adapting from 0.5 to 3 FPS based on bandwidth constraints), exports it as a JPEG, base64-encodes it, and sends it to the backend alongside the audio stream.

3. **WebSocket bridge** — The FastAPI backend receives the combined audio and video stream. It maintains one persistent WebSocket connection per user session and forwards data into an active Gemini Live API session.

4. **Gemini Live session** — The backend uses the **Google Agent Development Kit (ADK)** to manage the bidirectional Gemini streaming session. The ADK handles tool routing (e.g., dictionary lookups) and maintains the multi-turn conversation state. Audio chunks are forwarded to the model and video frames as images.

5. **Response audio** — Gemini returns audio responses as PCM at 24kHz. The backend streams these back to the browser over the WebSocket.

6. **Browser playback** — The browser decodes the incoming PCM data and schedules it for gapless playback using the Web Audio API's `AudioContext`, with timestamps tracked to avoid buffer underruns.

7. **Session state** — Firestore stores session metadata (start time, duration, language detected, end reason) for analytics and GCP service integration.

---

## Tutor Persona — LearnLM-Informed Pedagogy

SeeMe is a patient, encouraging tutor with a calm and warm voice. Its pedagogical design is grounded in Google's [LearnLM](https://ai.google.dev/gemini-api/docs/learnlm) learning science principles — research-backed guidelines for effective AI-assisted education. Built on Gemini 2.5 Flash, which has LearnLM capabilities natively infused, SeeMe aligns with all five core learning principles:

- **Active Learning** — Never gives the answer directly. SeeMe always responds with a guiding question: "What do you think happens when you multiply both sides by the same number?"
- **Cognitive Load Management** — Employs **progressive disclosure** (highlighting only the most critical error first rather than overwhelming the student) and keeps responses concise (2–3 sentences) while referencing what it can see in the student's work to stay grounded in context.
- **Learner Adaptation** — Reads the emotional room. If a student sounds frustrated, SeeMe slows down and breaks the problem into smaller steps. If they sound confident, it increases the challenge.
- **Curiosity Stimulation** — Connects solved problems to real-world contexts and asks "what if" questions to extend thinking beyond the immediate exercise.
- **Metacognitive Development** — Prompts students to reflect on their own thinking: "You got that one — what strategy did you use?" Builds independent learning skills, not just subject knowledge.

**Additional capabilities:**

- **Proactive observation** — Actively monitors the camera feed and comments without being asked. Catches mistakes in real time ("I see something in that second line — want to take another look?"), congratulates completed steps, and guides next actions — like having a tutor looking over your shoulder.
- **Visual grounding** — References what it sees via the camera: "I can see you've written 3x on the left side — what would you need to do to isolate x?"
- **Silence handling** — When a student goes quiet, checks in after a natural pause: "Still working on it? Take your time." Stays present without being pushy.
- **Multilingual** — Start speaking Portuguese, it responds in Portuguese. Switch to English mid-sentence, it follows. German works too.
- **Natural interruptions** — Because Gemini Live API is full-duplex, students can interrupt mid-response and SeeMe will stop, acknowledge, and re-approach — just like a real tutor would.

---

## Deployment to GCP

```bash
./deploy.sh
```

The deploy script performs the following steps automatically:

- Builds the Docker container image using Cloud Build
- Pushes the image to Artifact Registry
- Deploys the container to Cloud Run in `europe-west1`
- Binds `GEMINI_API_KEY` as a Cloud Run secret reference to Secret Manager
- Runs `firebase deploy` to publish the frontend PWA to Firebase Hosting
- Prints the live URLs for both the backend and frontend

After deployment:

- **Frontend:** `https://seeme-tutor.web.app`
- **Backend WebSocket:** `wss://seeme-tutor-[hash]-ew.a.run.app/ws`

---

## GCP Services Used

| GCP Service | How It Is Used |
|-------------|---------------|
| **Gemini 2.5 Flash Live API** | Core AI engine — real-time bidirectional audio and video streaming, multilingual response generation, Socratic tutoring logic |
| **Google ADK** | Agent Development Kit orchestrates the streaming session, routes tool calls, and manages state |
| **Cloud Run** | Serverless hosting for the FastAPI WebSocket backend; auto-scales to zero when idle, scales up on demand |
| **Firebase Hosting** | Hosts the PWA frontend on a global CDN; serves over HTTPS (required for camera/mic browser APIs) |
| **Firestore** | Stores session metadata (start time, duration, language detected, end reason) for analytics |
| **Secret Manager** | Stores the Gemini API key securely; Cloud Run mounts the secret at runtime via `--set-secrets` binding |
| **Cloud Build** | Builds the Docker container image from source on each deploy; no local Docker daemon required |
| **Artifact Registry** | Stores built container images; used as the image source for Cloud Run deployments |

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the values:

```
GEMINI_API_KEY=        # Your Gemini Developer API key
GCP_PROJECT_ID=seeme-tutor
GCP_REGION=europe-west1
FIRESTORE_COLLECTION=sessions
```

In production (Cloud Run), `GEMINI_API_KEY` is mounted from Secret Manager via `--set-secrets` and the other variables are set as Cloud Run environment variables.

---

## Hackathon Submission Notes

SeeMe Tutor was built for the **Gemini Live Agent Challenge** hosted by Google.

**Technology stack:** 100% Google and GCP.

- AI: Gemini 2.5 Flash Live API (`gemini-2.5-flash-native-audio-preview-12-2025`) via the `google-genai` Python SDK
- Agent Framework: Google Agent Development Kit (ADK) via `google-adk`
- Pedagogy: [LearnLM](https://cloud.google.com/solutions/learnlm)-informed system instructions aligned with Google's learning science research
- Backend: FastAPI on Cloud Run
- Frontend: Firebase Hosting
- Database: Firestore
- Secrets: Secret Manager

No third-party AI APIs are used. The entire intelligence layer runs through Google's Gemini platform, with pedagogical design grounded in [LearnLM's learning science principles](https://ai.google.dev/gemini-api/docs/learnlm).

---

## License

MIT License — see [LICENSE](LICENSE) for details.
