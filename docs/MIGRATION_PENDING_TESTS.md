# Backend Migration Pending Tests (Manual)

Source of truth: `backend/MIGRATION.md`  
Snapshot date: 2026-02-28

Use this file to run and mark every still-unfilled test from the migration doc.

## Test Environment

1. Start backend:
   - `cd backend`
   - `source .venv/bin/activate`
   - `uvicorn main:app --reload --port 8000`
2. Open frontend:
   - `http://localhost:8000`
3. Open browser console and keep backend logs visible.
4. Keep the new **Backend diagnostics** panel open in the UI while testing.

## Evidence to Capture

- Browser screenshot of relevant UI state (whiteboard, diagnostics panel, reconnect banner).
- Browser console snippet (if applicable).
- Backend stdout/debug log line proving tool/event fired.
- Optional: test report JSON file path under `backend/test_results/`.

---

## Step 0F — ADK Skeleton Validation (results still blank in MIGRATION)

| Test ID | Scenario | Expected | Result (PASS/FAIL) | Evidence |
|---|---|---|---|---|
| F1 | `GET /health` | 200 + `{"status":"ok"}` |  |  |
| F2 | WS connect | `backlog_context` arrives |  |  |
| F3 | Mic on + "Hello" | Tutor audio response |  |  |
| F4 | Camera on | No crash, video ingress increments |  |  |
| F5 | Phase transition | `set_session_phase` tool metric in logs |  |  |
| F6 | Ask "show me exercise 1" | `write_notes` + whiteboard card |  |  |
| F7 | Interrupt tutor mid-speech | `interrupted` event + stop speaking |  |  |
| F8 | 10s silence | Idle check-in appears |  |  |
| F9 | End session button | WS closes + ended reason stored |  |  |
| F10 | 20-minute limit | `session_limit` message |  |  |

---

## Step 4 — Whiteboard Sync (4.1–4.4 unfilled)

| Test ID | Scenario | Expected | Result (PASS/FAIL) | Evidence |
|---|---|---|---|---|
| 4.1 | Ask tutor to explain formula | `write_notes` called, card appears |  |  |
| 4.2 | Show same homework twice | Duplicate prevention (no re-capture) |  |  |
| 4.3 | Switch topic | `clear` action and board reset |  |  |
| 4.4 | Reconnect with previous notes | Notes restored from Firestore |  |  |

---

## Step 5 — Safety + Search Grounding (5.1–5.4 unfilled)

Use **Backend diagnostics** panel:
- `Guardrails` card/log for safety triggers
- `Grounding` card/log for citations

| Test ID | Scenario | Expected | Result (PASS/FAIL) | Evidence |
|---|---|---|---|---|
| 5.1 | "Just give me the answer" | Tutor redirects; guardrail event logged |  |  |
| 5.2 | Off-topic request | Tutor redirects to study scope |  |  |
| 5.3 | "Search for quadratic formula" | Search executes + grounding citation appears |  |  |
| 5.4 | Factual question without "search" | Answer from model knowledge, no grounding event |  |  |

---

## Step 7 — Latency + Resilience (7.1–7.3 unfilled)

Use **Backend diagnostics** panel:
- `Latency` card for metric + alerts
- Event log for `latency_event` / `latency_report`

| Test ID | Scenario | Expected | Result (PASS/FAIL) | Evidence |
|---|---|---|---|---|
| 7.1 | 5-minute session | latency events/reports emitted |  |  |
| 7.2 | Kill backend mid-session | Frontend reconnect banner appears |  |  |
| 7.3 | Short network drop (<5s) | Session resumes without full restart |  |  |

---

## Step 0G — Security Hardening (implemented, pending manual validation)

Implementation status in `MIGRATION.md`: marked implemented.

Track these once implemented:

| Check ID | Security Control | Expected | Result (PASS/FAIL) | Evidence |
|---|---|---|---|---|
| S1 | Restrictive CORS | No wildcard+credentials in production config |  |  |
| S2 | Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, CSP baseline |  |  |
| S3 | Rate limiting | Repeated abuse requests are throttled |  |  |
| S4 | Prompt injection guard | Instruction override attempts refused/logged |  |  |

---

## Sign-off

- Tester:
- Date:
- Build/commit:
- Remaining blockers:
