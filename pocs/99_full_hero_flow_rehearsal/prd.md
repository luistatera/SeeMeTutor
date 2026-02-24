# POC 99 - Full Hero Flow Rehearsal: Mini PRD

## Why This Matters

POC 99 is the integration test that determines whether SeeMe Tutor wins or
loses the hackathon. Individual capabilities (proactive vision, whiteboard,
interruption handling, search grounding) are worthless if they cannot work
together in a single uninterrupted session.

The demo video is 30% of the judging score. A single failure during the
scripted demo flow - a note that does not appear, an interruption that is
ignored, a citation that never renders - is immediately visible to judges.
This POC exists to make that failure impossible by rehearsing the exact
sequence repeatedly until it is reliable.

---

## The Problem (Without This POC)

Each prior POC validates a capability in isolation. But integration introduces
failure modes that do not exist in isolation:

| # | Integration failure | User-visible impact |
|---|---|---|
| 1 | Whiteboard tool call blocks audio streaming | Tutor goes silent for 1-2s after writing a note |
| 2 | Search grounding + proactive vision conflict | Tutor tries to search while also responding to idle nudge |
| 3 | Interruption cancels whiteboard dispatch | Note queued but never delivered after barge-in |
| 4 | Reconnect drops tool declarations | Tutor cannot write notes or search after reconnect |
| 5 | Metrics from different subsystems overwrite each other | Dashboard shows wrong counts |
| 6 | Hidden turn from idle orchestrator collides with tool response | Gemini receives malformed input |

**If any of these happen during the 4-minute demo video, the submission is dead.**

---

## What "Done" Looks Like

### Must-Have (blocks submission)

| ID | Criterion | How to verify |
|---|---|---|
| **M1** | Full demo flow completes without restart | Run scripted flow 3 times, all 6 checklist items hit each time |
| **M2** | Proactive vision triggers within 8s of camera + silence | Point camera at homework, stay silent, tutor speaks up |
| **M3** | Whiteboard note appears while tutor is speaking | Note card renders with "Live" badge, not "Delayed" |
| **M4** | Interruption stops audio within ~300ms | Say "wait" during tutor speech, audio stops, tutor acknowledges |
| **M5** | Search citation renders as card + toast | Ask "What is the atomic number of carbon?" - citation appears |
| **M6** | Action moment detected after 3+ exchanges | Have a back-and-forth tutoring conversation |
| **M7** | Reconnect restores context | Click "Sim Reconnect", tutor resumes without fresh greeting |
| **M8** | No capability interferes with another | Whiteboard + interruption + search all work in same session |

### Should-Have (improve demo quality)

| ID | Criterion | How to verify |
|---|---|---|
| S1 | Demo checklist shows 6/6 green | All items checked in a single session |
| S2 | Metrics dashboard accurate | Server metrics match observed events |
| S3 | Transcript captures all speech | Both student and tutor transcriptions visible |
| S4 | No internal control text leaks | Tutor never says "INTERNAL CONTROL" or "[SYSTEM...]" |
| S5 | Mid-session restart guard active | After 6+ turns, tutor never restarts with "Welcome!" |

### Won't Do (out of scope for POC)

- Client-side VAD (Silero) - tested in POC 01, not merged here to keep scope tight
- Visual change detection from browser - tested in POC 02
- Firestore persistence - production concern, not demo-critical
- Mobile responsive layout - demo runs on laptop

---

## Scripted Demo Flow

This is the exact sequence the demo video will follow. POC 99 must execute
this sequence reliably.

### Scene 1: Proactive Vision (0:00 - 0:45)

1. Start session with camera pointing at math homework
2. Stay silent for ~6 seconds
3. **Expected:** Tutor speaks up proactively, referencing what it sees
4. **Checklist:** "Proactive Vision triggered" turns green

### Scene 2: Whiteboard Note (0:45 - 1:30)

1. Ask the tutor to explain the current problem
2. **Expected:** Tutor explains while simultaneously writing a note to the whiteboard
3. Note card appears with "Live" sync badge
4. **Checklist:** "Whiteboard note received" turns green

### Scene 3: Interruption (1:30 - 2:00)

1. While tutor is explaining, say "wait, wait"
2. **Expected:** Tutor stops immediately, acknowledges ("Sure!" / "Go ahead!")
3. Ask a different question
4. **Expected:** Tutor follows the new topic
5. **Checklist:** "Interruption handled" turns green

### Scene 4: Search Citation (2:00 - 2:45)

1. Ask a factual question: "What is the formula for the area of a circle?"
2. **Expected:** Tutor uses Google Search, responds with verified fact
3. Citation card + floating toast appear
4. **Checklist:** "Search citation shown" turns green

### Scene 5: Action Moment (2:45 - 3:15)

1. Have a 3+ turn back-and-forth with the tutor
2. Student attempts the problem, tutor guides
3. **Expected:** After 3 exchanges, action moment is detected
4. **Checklist:** "Action moment completed" turns green

### Scene 6: Reconnect (3:15 - 3:45)

1. Click "Sim Reconnect" button
2. Overlay shows "Reconnecting..."
3. After 2 seconds, WS reconnects
4. Transcript shows "Session context restored"
5. **Expected:** Tutor continues without restarting the greeting
6. **Checklist:** "Reconnect survived" turns green

### Close (3:45 - 4:00)

- Dashboard shows all 6/6 checklist items green
- Metrics visible: proactive triggers, whiteboard notes, interruptions, citations

---

## Architecture

```
Browser                                FastAPI (port 9900)
  |                                        |
  |-- audio (PCM 16kHz) -------->         |-- forward to Gemini Live API
  |-- video (JPEG 2fps) -------->         |-- forward to Gemini Live API
  |-- barge_in ----------------->         |-- track interruption metrics
  |-- speech_start/end --------->         |-- update idle orchestrator state
  |-- tutor_playback_start/end ->         |-- sync whiteboard dispatch
  |-- resume_context ----------->         |-- inject hidden turn for continuity
  |                                        |
  |<-- audio (PCM 24kHz) -------          |<-- Gemini audio response
  |<-- text / transcripts ------          |<-- Gemini text + transcriptions
  |<-- whiteboard (note card) --          |<-- write_notes tool result (queued dispatch)
  |<-- grounding (citation) ----          |<-- Google Search metadata
  |<-- interrupted -------------          |<-- Gemini server-side interruption
  |<-- proactive_trigger -------          |<-- Proactive speech detection
  |<-- idle_poke / idle_nudge --          |<-- Idle orchestrator events
  |<-- metrics -----------------          |<-- Unified metric snapshot
  |<-- demo_checklist ----------          |<-- Checklist item updates
```

### Concurrent Backend Tasks

1. **Browser -> Gemini** - Audio/video forwarding, speech state, barge-in, reconnect
2. **Gemini -> Browser** - Audio, text, tool calls, grounding, interruptions, transcriptions
3. **Idle Orchestrator** - Silence monitoring, soft poke, hard nudge escalation
4. **Whiteboard Dispatcher** - Queued note delivery, speech-sync timing

---

## Key Metrics (Tracked per Session)

| Category | Metrics |
|---|---|
| Proactive Vision | triggers (organic vs nudge), silence duration, backend pokes/nudges |
| Whiteboard | tool calls, notes sent, dedupe blocks, sync rate, delivery latency |
| Interruption | Gemini interruptions, VAD barge-ins, latency (client + server) |
| Grounding | search events, citations sent, search queries |
| General | turns, audio chunks in/out, video frames, internal text filtered |
| Demo | checklist completion (6 items), session duration |

---

## Risk Mitigation

### Risk 1: Tool conflicts

**Mitigation:** write_notes and google_search are declared as separate Tool
objects. Gemini handles them independently. The backend processes tool_call
for write_notes and extracts grounding metadata from message-level attributes.
No collision.

### Risk 2: Hidden turns during tool response

**Mitigation:** The idle orchestrator checks both `assistant_speaking` and
`client_tutor_playing` before sending hidden turns. If the model is
processing a tool response, it will be marked as speaking (or about to
speak), preventing a collision.

### Risk 3: Reconnect loses tool declarations

**Mitigation:** Each WebSocket connection creates a fresh Gemini session
with full config including all tools. There is no state to lose because
every session starts clean with the same config.

### Risk 4: Demo checklist not completing

**Mitigation:** The action moment threshold is set to 3 exchanges (low
bar). Proactive vision fires within 6-9 seconds. Citation requires a
factual question (scripted). Reconnect is manual button press. All
items are controllable by the demo operator.

---

## Timeline

POC 99 is the final integration gate before the demo video recording.
It should be validated multiple times with the scripted flow before
any recording begins.

**Success criteria:** 3 consecutive full runs with 6/6 checklist
completion, no crashes, no capability interference.
