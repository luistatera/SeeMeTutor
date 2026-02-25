# POC 10 — Screen Share Toggle: Mini PRD

## Why This Matters

Many students work on **digital worksheets, online textbooks, and browser-based exercises** where a camera pointed at a screen produces blurry, glare-ridden, unreadable frames. Screen share gives the tutor pixel-perfect visibility of digital content, dramatically improving visual grounding accuracy.

From the judging rubric (40% weight — Innovation & Multimodal UX):

> "visual precision; experience fluidity & context-awareness"

If the tutor squints at a camera image of a laptop screen and misreads "7" as "1", the tutoring quality collapses. Screen share eliminates this failure mode entirely for digital work while keeping camera available for physical homework.

---

## The Problem (Without This POC)

| # | Broken behavior | User impact | Root cause |
|---|---|---|---|
| 1 | Camera captures screen with glare/blur | Tutor misreads text, equations, diagrams | Optical path: camera → screen reflection → JPEG compression |
| 2 | No way to show digital worksheets clearly | Student must print everything or hold laptop awkwardly | Only camera input available |
| 3 | Switching input requires session restart | Student loses context, tutor forgets what they were working on | No runtime source switching |
| 4 | No privacy indicator for screen share | Student doesn't know what the tutor can see | No source awareness in UI |
| 5 | Screen share permission denied crashes flow | Student stuck with no visual input | No fallback handling |

---

## What "Done" Looks Like

### Must-Have (POC passes)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | Single toggle switches between Camera and Screen | Click toggle button — source changes instantly |
| **M2** | Switching is instant (< 500ms perceived) | Measure from click to first frame of new source arriving at backend |
| **M3** | Session does not reset on switch — audio continues uninterrupted | Switch mid-conversation — tutor doesn't restart greeting or lose context |
| **M4** | Tutor acknowledges switch with 1 line | After switch, tutor says "Ok, I can see your screen now" or similar |
| **M5** | LIVE badge shows what is being shared | Badge reads "LIVE - CAMERA" or "LIVE - SCREEN" with red indicator |
| **M6** | Stop Sharing button works | Click "Stop Sharing" while in screen mode — reverts to camera |
| **M7** | Permission denied does not crash | Deny screen share permission — stays on camera with error message |
| **M8** | Browser "Stop sharing" UI detected | Use browser's built-in stop sharing button — app detects and reverts |
| **M9** | Proactive vision works on both sources | Tutor proactively comments on visible work from camera AND screen |

### Should-Have (improve quality but not blockers)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Screen frames are crisp (readable text) | Share a PDF with small text — tutor can read it accurately |
| S2 | Frame rate stays at 1 FPS without spikes | Check metrics dashboard — consistent frame delivery |
| S3 | Switch latency metrics visible in UI | Dashboard shows avg switch latency |

### Won't Do (out of scope)

- Voice command "stop sharing" (requires NLU pipeline — future feature)
- Simultaneous camera + screen PiP (adds complexity with no demo value)
- Application window selection (getDisplayMedia handles this at browser level)
- Tab audio capture (audio comes from mic, not screen)

---

## Key Metrics

### Primary (must track in logs)

| Metric | Target | How measured |
|---|---|---|
| **Switch latency (client)** | < 500ms | `performance.now()` delta from toggle click to new source active |
| **Source switches without reconnect** | 5+ in a row with 0 reconnects | Count switches with 0 WS disconnects |
| **Audio continuity during switch** | 0 dropped audio chunks | Check audio_chunks_in counter is monotonically increasing through switches |
| **Tutor acknowledgement rate** | 100% of switches | Tutor speaks within 5s of source switch |
| **Permission denied recovery** | 100% | App stays functional after denied screen share |

### Secondary (nice to see)

| Metric | Target | How measured |
|---|---|---|
| Screen frame readability | Text legible in JPEG at 0.80 quality | Manual visual inspection of captured frames |
| Proactive triggers on screen input | > 0 per session | Count proactive_triggers where active_source=screen |
| Avg switch latency | < 300ms | Average of all switch_latency_ms values |

---

## Architecture Summary

```
+--------------------------------------------------------------------+
|                         BROWSER                                      |
|                                                                      |
|  Camera (getUserMedia) ----+                                         |
|                            |---> Frame Capture (1 FPS) ---> WS       |
|  Screen (getDisplayMedia) -+     (camera_frame / screen_frame)       |
|                                                                      |
|  Toggle Button: Camera <---> Screen                                  |
|  - Starts new source BEFORE stopping old source                      |
|  - Sends source_switch message to backend                            |
|  - Measures switch latency (performance.now)                         |
|                                                                      |
|  Mic ---> PCM 16kHz ----------------------------------------> WS     |
|  Speaker <--- AudioContext <--- Playback Queue <------------- WS     |
|  (audio NEVER interrupted during source switch)                      |
|                                                                      |
|  UI: LIVE badge, source label, preview PiP, error banner             |
+--------------------------------------------------------------------+
                              |
                              v
+--------------------------------------------------------------------+
|                    FASTAPI (WebSocket)                                |
|                                                                      |
|  camera_frame ---> Gemini Live API (video input)                     |
|  screen_frame ---> Gemini Live API (video input)                     |
|  (both use same video=Blob pathway)                                  |
|                                                                      |
|  source_switch ---> inject hidden turn                               |
|    "to screen" -> "I can see your screen now"                        |
|    "to camera" -> "I'm back to your camera"                          |
|                                                                      |
|  stop_sharing ---> inject hidden turn                                |
|    "Visual input stopped, continue voice-only"                       |
|                                                                      |
|  Idle Orchestrator: same poke/nudge logic, checks ANY visual source  |
|  Logging: all switches in JSONL + details.log + transcript.log       |
+--------------------------------------------------------------------+
```

**Key design decisions:**

1. **Same Gemini video input for both sources.** Camera frames and screen frames are both sent as `image/jpeg` via `send_realtime_input(video=...)`. Gemini doesn't need to know the source — it just sees the image. The hidden turn tells it *what* it's looking at.

2. **New source starts BEFORE old source stops.** This ensures zero gap in visual input. The brief overlap (one frame from each source) is preferable to a gap.

3. **Audio is completely decoupled from visual switching.** Mic capture and playback are on separate pipelines that are never touched during a source switch.

4. **Hidden turn injection for context.** Instead of relying on Gemini to detect the visual change organically (which is slow and unreliable), we inject a synthetic user turn that tells Gemini the source changed. This produces the immediate acknowledgement required by the spec.

---

## What Ships to Main App

### Backend
- `camera_frame` and `screen_frame` message types (both forwarded as video input)
- `source_switch` handler with hidden turn injection
- `stop_sharing` handler with voice-only fallback prompt
- Metrics: `source_switches`, `switch_to_screen_count`, `switch_to_camera_count`

### Frontend
- `getDisplayMedia` screen capture with frame extraction
- Toggle button with instant source switching
- LIVE badge and source label
- Stop Sharing button + browser stop detection (`track.ended` event)
- Permission denied handling with error banner and camera fallback
- Switch latency measurement

### NOT shipped (POC-only)
- Metrics dashboard (debug tool)
- JSONL file logging (debug tool)

---

## Test Plan (Ordered by Priority)

| # | Scenario | Pass criteria | Tests M# |
|---|---|---|---|
| 1 | **Basic toggle** — Start on camera, click toggle to screen, click back to camera | Source switches both ways, LIVE badge updates, preview changes | M1, M5 |
| 2 | **5 rapid switches** — Toggle 5 times in 10 seconds | 0 WS disconnects, 0 audio drops, metrics show 5 switches | M1, M2, M3 |
| 3 | **Tutor acknowledges switch** — Switch to screen while tutor is idle | Tutor says "I can see your screen now" within 5s | M4 |
| 4 | **Audio continuity** — Switch source while tutor is speaking | Tutor audio plays without gap or interruption | M3 |
| 5 | **Permission denied** — Deny screen share when prompted by browser | Error banner shows, stays on camera, session continues | M7 |
| 6 | **Browser stop sharing** — Use browser's built-in "Stop sharing" overlay | App detects, reverts to camera, tutor notified | M8, M6 |
| 7 | **Screen readability** — Share a document with 12pt text | Tutor accurately reads/references text content | M9, S1 |
| 8 | **Proactive on screen** — Share screen with visible work, stay silent | Tutor proactively comments on screen content within 10s | M9 |

---

## Risk: getDisplayMedia Compatibility

`getDisplayMedia` requires a user gesture and HTTPS (or localhost). The browser picker UI varies across Chrome, Firefox, Safari. Key risks:

- **Safari:** Limited getDisplayMedia support. May not support window/tab selection.
- **Firefox:** Works but picker UI is different from Chrome.
- **Mobile browsers:** getDisplayMedia is not available on most mobile browsers.

**Mitigation:** POC targets Chrome desktop only (demo will use Chrome). Mobile gracefully hides the toggle button if `getDisplayMedia` is not available.

---

## Timeline

This POC is a **Week 2** deliverable (Feb 24 - Mar 2: "Live camera feed + visual grounding").
Screen share is an enhancement to the camera pipeline validated in POC 02. Integration into the main app follows POC validation.
