#!/usr/bin/env bash
# =============================================================================
# SeeMe Tutor — One-Command Deploy Script
# =============================================================================
# Deploys the full stack to Google Cloud Platform:
#   - Backend  → Cloud Run  (europe-west1)
#   - Frontend → Firebase Hosting
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (`gcloud auth login`)
#   - firebase CLI installed (`npm install -g firebase-tools`)
#   - Secret "gemini-api-key" exists in Secret Manager for project seeme-tutor
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}${YELLOW}▶  $*${RESET}"; }

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo -e ""
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e "${BOLD}${GREEN}   SeeMe Tutor — Deploying to Google Cloud          ${RESET}"
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e ""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID="seeme-tutor"
REGION="europe-west1"
SERVICE_NAME="seeme-tutor"
SERVICE_ACCOUNT="seeme-tutor-sa@seeme-tutor.iam.gserviceaccount.com"
SECRET_GEMINI="gemini-api-key"
SECRET_DEMO_CODE="demo-access-code"
MEMORY="512Mi"
TIMEOUT="300"
MIN_INSTANCES="0"
MAX_INSTANCES="10"
PORT="8080"

# ---------------------------------------------------------------------------
# Step 0 — Prerequisite checks
# ---------------------------------------------------------------------------
step "Checking prerequisites"

if ! command -v gcloud &>/dev/null; then
    error "gcloud CLI not found. Install it from https://cloud.google.com/sdk/docs/install"
fi
success "gcloud CLI found: $(gcloud version --format='value(Google Cloud SDK)' 2>/dev/null | head -1)"

if ! command -v firebase &>/dev/null; then
    error "firebase CLI not found. Install it with: npm install -g firebase-tools"
fi
success "firebase CLI found: $(firebase --version 2>/dev/null)"

# Verify gcloud is authenticated
ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
    error "No active gcloud account. Run: gcloud auth login"
fi
success "Authenticated as: ${ACTIVE_ACCOUNT}"

# ---------------------------------------------------------------------------
# Step 1 — Set active project
# ---------------------------------------------------------------------------
step "Setting GCP project to ${PROJECT_ID}"

gcloud config set project "${PROJECT_ID}" --quiet
success "Active project: ${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 2 — Verify Gemini API key exists in Secret Manager
# ---------------------------------------------------------------------------
step "Verifying secrets in Secret Manager"

for SECRET in "${SECRET_GEMINI}" "${SECRET_DEMO_CODE}"; do
    gcloud secrets versions access latest \
        --secret="${SECRET}" \
        --project="${PROJECT_ID}" &>/dev/null || \
        error "Failed to read secret '${SECRET}' from Secret Manager. \
Ensure it exists: gcloud secrets create ${SECRET} --project=${PROJECT_ID}"
    success "Secret '${SECRET}' verified in Secret Manager"
done

# ---------------------------------------------------------------------------
# Step 3 — Build and deploy backend to Cloud Run
# ---------------------------------------------------------------------------
step "Deploying backend to Cloud Run (region: ${REGION})"
info "This builds the container via Cloud Build and deploys to Cloud Run..."
info "Source: backend/"
info "Secrets will be mounted from Secret Manager at runtime"

# Build from project root so Dockerfile can COPY frontend/ alongside backend/
cp backend/Dockerfile .
gcloud run deploy "${SERVICE_NAME}" \
    --source . \
    --region="${REGION}" \
    --allow-unauthenticated \
    --service-account="${SERVICE_ACCOUNT}" \
    --set-secrets="GEMINI_API_KEY=${SECRET_GEMINI}:latest,DEMO_ACCESS_CODE=${SECRET_DEMO_CODE}:latest" \
    --set-env-vars="GCP_PROJECT_ID=${PROJECT_ID},GCP_REGION=${REGION}" \
    --port="${PORT}" \
    --memory="${MEMORY}" \
    --timeout="${TIMEOUT}" \
    --min-instances="${MIN_INSTANCES}" \
    --max-instances="${MAX_INSTANCES}" \
    --project="${PROJECT_ID}" \
    --quiet
rm Dockerfile

success "Cloud Run deploy complete"

# ---------------------------------------------------------------------------
# Step 4 — Retrieve the live Cloud Run URL
# ---------------------------------------------------------------------------
step "Fetching Cloud Run service URL"

BACKEND_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --format="value(status.url)" 2>/dev/null)

if [[ -z "${BACKEND_URL}" ]]; then
    warn "Could not automatically retrieve Cloud Run URL. Check the GCP Console."
else
    success "Backend URL: ${BACKEND_URL}"
fi

# ---------------------------------------------------------------------------
# Step 5 — Inject backend URL into frontend and deploy to Firebase Hosting
# ---------------------------------------------------------------------------
step "Deploying frontend to Firebase Hosting"

# Extract host from Cloud Run URL so the Firebase-hosted frontend can reach
# the WebSocket endpoint on Cloud Run (Firebase Hosting cannot proxy WS).
if [[ -n "${BACKEND_URL:-}" ]]; then
    BACKEND_HOST="${BACKEND_URL#https://}"
    info "Injecting backend-host meta tag: ${BACKEND_HOST}"
    # Insert backend-host meta tag after charset meta (works on both macOS and Linux sed)
    sed -i.bak "s|<meta charset=\"UTF-8\" />|<meta charset=\"UTF-8\" /><meta name=\"backend-host\" content=\"${BACKEND_HOST}\" />|" \
        frontend/index.html
fi

info "Deploying from firebase.json config (public dir: frontend/)"

firebase deploy --only hosting --project "${PROJECT_ID}"

# Restore original frontend (remove injected meta tag)
if [[ -f frontend/index.html.bak ]]; then
    mv frontend/index.html.bak frontend/index.html
fi

success "Firebase Hosting deploy complete"

# ---------------------------------------------------------------------------
# Step 6 — Retrieve Firebase Hosting URL
# ---------------------------------------------------------------------------
FIREBASE_URL="https://${PROJECT_ID}.web.app"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo -e ""
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e "${BOLD}${GREEN}   Deployment complete!                              ${RESET}"
echo -e "${BOLD}${GREEN}=====================================================${RESET}"
echo -e ""
echo -e "  ${BOLD}Backend  (Cloud Run):${RESET}       ${BACKEND_URL:-"check GCP Console"}"
echo -e "  ${BOLD}Frontend (Firebase):${RESET}        ${FIREBASE_URL}"
echo -e ""
echo -e "  Health check: ${CYAN}curl ${BACKEND_URL}/health${RESET}"
echo -e ""
success "SeeMe Tutor is live on Google Cloud."
echo -e ""
