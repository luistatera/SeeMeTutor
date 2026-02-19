# SeeMe Tutor — Backend Dockerfile
#
# Build context must be the project root:
#   docker build -f backend/Dockerfile -t seeme-tutor .
#
# Cloud Run deployment uses `gcloud run deploy --source .` which builds from
# the project root. The frontend is served as static files from FastAPI, so
# it is copied in from the project root during this build.

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies required by some Python packages
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies first (maximise layer cache hits)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code
COPY backend/ .

# Copy compiled frontend assets so FastAPI can serve them as static files
# The app expects them at /app/frontend relative to the working directory
COPY frontend/ ./frontend/

# Cloud Run injects PORT; default to 8080 if not set
ENV PORT=8080

EXPOSE 8080

# Single worker: Gemini Live sessions are stateful per WebSocket connection,
# so all requests for a session must reach the same process.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
