# POC 07 — Latency Instrumentation & Budget: Mini PRD

## Why This Matters

"Live" is subjective unless you show evidence. Every hackathon submission in the Live Agents category will _claim_ real-time interaction, but very few will **prove** it with numbers. Judges explicitly score for "experience fluidity" (40% weight), and an always-visible latency HUD turns a subjective impression into objective proof.

From the judging rubric:

> "experience fluidity & context-awareness"

If we can show response-start latency averaging under 500ms and interruption-stop under 200ms — with live, color-coded evidence on screen — we demonstrate engineering rigor that separates SeeMe from talk-only demos.

---

## The Problem (Without This POC)

The main app today has **no visibility into timing performance**:

| # | Problem | User / judge impact | Root cause |
|---|---|---|---|
| 1 | No response latency tracking | "Is that lag normal?" — judges can't tell | No timestamps around turn boundaries |
| 2 | No interruption timing proof | Interruptions feel fast but could be 800ms+ | No measurement from barge-in to Gemini confirmation |
| 3 | No regression detection | A slow backend deploy could go unnoticed | No threshold alerts or budget enforcement |
| 4 | No exportable evidence | Demo video shows subjective "feels fast" | No summary table for JUDGES.md or blog post |
| 5 | Latency spikes invisible | One bad turn hides in a good session | No min/max/p95 tracking |

**In the demo video, a visible latency HUD is the difference between "trust me, it's fast" and "look, here's proof."**

---

## What "Done" Looks Like

### Must-Have (POC ships to main app)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | Response Start Latency tracked | Speak, wait for tutor reply; HUD shows latency in ms. Server logs include the measurement. |
| **M2** | Interruption Stop Latency tracked | Interrupt the tutor mid-speech; HUD shows time from barge-in to Gemini interrupted event. |
| **M3** | Latency HUD visible and toggleable | A semi-transparent overlay shows current / avg / p95 for each metric with color coding. |
| **M4** | Alerts on threshold breach | Force a slow response (e.g., complex question); if > 800ms, HUD flashes red and event log shows alert. |
| **M5** | Session summary exportable | Click "Export" or end session; a summary table appears with all latency stats per metric. |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Turn-to-Turn latency tracked | Time from turn_complete to next student speech start. |
| S2 | First Byte Latency (one-time) | Time from session open to first tutor audio chunk. |
| S3 | JSONL session logs with all timing data | Inspect `logs/` directory after session. |

### Won't Do (out of scope)

- Network-level latency profiling (RTT, jitter) — requires infrastructure changes.
- Video frame latency instrumentation — separate POC concern.
- Automated performance regression CI — post-hackathon.

---

## Key Metrics

### Primary (must track in logs and HUD)

| Metric | Target | Alert Threshold | How measured |
|---|---|---|---|
| **Response Start** | < 500ms | > 800ms | Time from last student audio chunk (or speech_end) to first tutor audio chunk |
| **Interruption Stop** | < 200ms | > 400ms | Time from barge_in message to Gemini interrupted event |
| **Visual Comment** | < 1500ms | > 2500ms | Reserved for camera-to-speech latency (future) |

### Secondary (tracked in logs)

| Metric | Target | How measured |
|---|---|---|
| **Turn-to-Turn** | < 2000ms | Time from turn_complete to next student speech start |
| **First Byte** | < 3000ms | Time from session start to first tutor audio chunk |

### Aggregate Stats (per metric)

- Current (last measurement)
- Average (running mean)
- P95 (95th percentile)
- Min / Max

---

## Architecture Summary

```
+-----------------------------------------------------------------+
|                         BROWSER                                  |
|                                                                  |
|  Mic --> PCM capture --> WebSocket --> Server                    |
|                                                                  |
|  performance.now() timestamps at:                                |
|    - last audio chunk sent (student speaking)                   |
|    - speech_end detected                                        |
|    - first tutor audio chunk received                           |
|    - barge_in sent                                              |
|    - interrupted received                                       |
|                                                                  |
|  Latency HUD (overlay):                                         |
|    Response Start: 342ms / avg 410ms / p95 620ms   [GREEN]      |
|    Interruption:   180ms / avg 195ms               [GREEN]      |
|    Turn Gap:       890ms / avg 1.1s                [YELLOW]     |
|    First Byte:     2.1s                            [GREEN]      |
|                                                                  |
|  Metric Cards: Response Avg | Interrupt Avg | Turns | Alerts    |
+-----------------------------------------------------------------+
                            |
                            v
+-----------------------------------------------------------------+
|                    FASTAPI (WebSocket)                            |
|                                                                  |
|  Timestamps at:                                                  |
|    - last_audio_in_at (student audio received)                  |
|    - first_audio_out_at (first tutor chunk sent)                |
|    - turn_complete_at                                           |
|    - barge_in_at                                                |
|    - interrupted_at                                             |
|                                                                  |
|  Computes: response_start_ms, interruption_stop_ms              |
|  Maintains: running stats (min, max, avg, p95)                  |
|  Sends: latency_report on each turn_complete                    |
|  Sends: latency_alert when threshold exceeded                   |
|  Logs: JSONL with all timing data                               |
+-----------------------------------------------------------------+
```

**Dual measurement:**
1. **Server-side** (authoritative) — Python timestamps on the WebSocket handler, closest to actual Gemini round-trip.
2. **Client-side** (supplementary) — `performance.now()` in the browser, includes network overhead.

Both are logged. The HUD shows server-reported stats (from `latency_report` messages) with client-side overlays for responsiveness.

---

## What Ships to Main App

### Backend
- Timestamp tracking at turn boundaries (minimal overhead — just `time.time()` calls)
- Running stats computation (reusable utility)
- `latency_report` message type on `turn_complete`
- `latency_alert` message type on threshold breach
- JSONL logging of all timing data

### Frontend
- Latency HUD component (toggleable, semi-transparent overlay)
- Color-coded budget indicators (green/yellow/red)
- Session summary export (for JUDGES.md screenshots)

### NOT shipped (POC-only)
- Detailed event log with per-chunk timestamps
- Debug metric cards (replaced by minimal HUD in production)

---

## Budget Thresholds

| Metric | Green (within budget) | Yellow (close) | Red (exceeded) |
|---|---|---|---|
| Response Start | < 500ms | 500-800ms | > 800ms |
| Interruption Stop | < 200ms | 200-400ms | > 400ms |
| Visual Comment | < 1500ms | 1500-2500ms | > 2500ms |
| Turn-to-Turn | < 1500ms | 1500-2500ms | > 2500ms |
| First Byte | < 3000ms | 3000-5000ms | > 5000ms |

---

## Timeline

This POC is a **Week 3** deliverable. The latency HUD integrates into the main app as a toggleable overlay for the demo video — proving "live" performance with hard numbers rather than subjective claims.
