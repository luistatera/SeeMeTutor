# Repository Guidelines

## Project Structure & Module Organization
Core application code lives in `backend/` and `frontend/`.
- `backend/main.py`: FastAPI app, WebSocket bridge, static file serving, health endpoint.
- `backend/gemini_live.py` and `backend/tutor_agent/`: Gemini Live session orchestration and tutor behavior.
- `frontend/index.html`: single-page PWA UI (HTML/CSS/JS).
- `infrastructure/`: GCP helper scripts and service integration examples.
- `pocs/`: proof-of-concept experiments and scenario notes (`main.py`, `rules.md`, `test.md` per PoC).
- `.github/workflows/deploy.yml`: production deploy pipeline (runs `deploy.sh` on push to `main`).

## Build, Test, and Development Commands
- `cd backend && python3 -m venv .venv && source .venv/bin/activate`: create/activate local env.
- `pip install -r backend/requirements.txt`: install backend dependencies.
- `cd backend && uvicorn main:app --reload --port 8000`: run locally at `http://localhost:8000`.
- `curl http://localhost:8000/health`: quick backend health check.
- `./deploy.sh`: deploy backend (Cloud Run) + frontend (Firebase Hosting).

Prerequisite for local Gemini access: `gcloud auth application-default login`.

## Coding Style & Naming Conventions
Use clear, minimal changes and preserve existing patterns.
- Python: PEP 8, 4-space indentation, `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants.
- Prefer type hints for new backend functions.
- Frontend (`frontend/index.html`): keep 2-space indentation and existing sectioned comment blocks.
- Keep modules focused; avoid adding cross-cutting utility code unless reused.

## Testing Guidelines
There is no formal automated test suite yet.
- For backend changes, run the app locally and verify `/health`, WebSocket connect flow, and regressions in logs.
- For UI changes, validate mic/camera permissions and audio round-trip in Chrome or Edge.
- For new logic, add targeted tests under `tests/` using `test_<feature>.py` naming when feasible.

## Commit & Pull Request Guidelines
Recent history favors concise, imperative commits with prefixes (`feat:`, `fix:`, `style:`) plus PoC-specific updates.
- Commit example: `feat: add idle timeout guard for websocket sessions`.
- Keep one logical change per commit.
- PRs should include: purpose, key files changed, manual validation steps, and screenshots/GIFs for UI updates.
- Link related issue(s) and note any GCP/Firebase deploy impact.

## Security & Configuration Tips
- Never commit `.env` or secrets; update `.env.example` when adding config.
- Use Secret Manager for production secrets (for example `DEMO_ACCESS_CODE`).
- Preserve privacy safeguards (no raw audio/video persistence; avoid logging sensitive user data).
