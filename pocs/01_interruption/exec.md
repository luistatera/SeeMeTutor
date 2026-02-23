# POC 01 — Interruption Handling: Execution Log

## Files
- `main.py` — FastAPI backend (~426 LOC)
- `index.html` — Frontend with VAD + playback cancellation (~753 LOC)

## Run Command
```bash
cd pocs/01_interruption
../../backend/.venv/bin/uvicorn main:app --reload --port 8100
# Open http://localhost:8100
```

---

## Architecture: Two-Layer Interruption

### Layer 1 — Client-side VAD (instant, ~1-5ms)
- Silero VAD v5 via `@ricky0123/vad-web@0.0.30` (CDN, no build step)
- `onSpeechStart` creates a **barge-in candidate**, then confirms speech for ~220ms
- Barge-in only fires if mic loudness is above ambient noise floor + margin
- Confirmed barge-in calls `cancelPlayback()` and sends `barge_in` with latency
- Sends `barge_in` to backend with `client_latency_ms`
- VAD toggle checkbox for A/B testing

### Layer 2 — Gemini server-side (confirmation, ~150-500ms)
- Gemini's own VAD sends `interrupted` event
- Backend logs `vad_to_gemini_ms` — delay between VAD and Gemini detection
- Fallback when VAD is disabled

### Key Design: VAD as Audio Gate
VAD doesn't just detect barge-in — it **gates audio to Gemini**:
- VAD detects NO speech → sends silence (zero-filled PCM) to Gemini
- VAD detects speech → sends real audio to Gemini
- Background noise (fans, traffic) never reaches Gemini

---

## Issues Found & Fixed

### Issue 1: Fan Noise Causing False Interruptions
**Symptom:** Tutor stops mid-speech saying "I paused because I thought you
might have a question" — triggered by fan noise, not student speech.

**Root cause:** Raw mic audio (including fan noise) streamed directly to
Gemini. `automatic_activity_detection` with `START_SENSITIVITY_HIGH` was
too aggressive — classified fan noise as speech.

**Fix:**
1. VAD audio gate — only real speech reaches Gemini, silence otherwise
2. Raised VAD `positiveSpeechThreshold` 0.3 → 0.72 (stricter voice detection)
3. Lowered Gemini `start_of_speech_sensitivity` HIGH → LOW
4. Added barge-in confirmation window (`220ms`) before cancelling playback
5. Added adaptive mic loudness gate (voice must exceed noise floor + margin)
6. Increased Gemini `silence_duration_ms` 500 → 700

### Issue 2: VAD Never Fired (0 Barge-ins)
**Symptom:** VAD checkbox enabled but 0 VAD barge-ins in all tests.

**Root cause:** `MicVAD.new()` called at page load → requests mic permission
→ browsers block without user gesture → fails silently.

**Fix:** Lazy VAD init via `ensureVAD()` called inside `startMic()` (button
click = user gesture). Only runs once.

### Issue 3: Acoustic Echo Causing Self-Interruption
**Symptom:** Tutor stops speaking after only a few words with `spoke: 0ms` in
Gemini interruption events. Student says "Why did you stop?" — nobody interrupted.

**Evidence from test:**
```
08:07:28 Tutor: measured?
08:07:31 GEMINI INT — lat: 198ms, spoke: 0ms
08:07:33 Student: Why did you stop?
08:07:34 Tutor: Got it! I paused because I thought you might want to know
```

**Root cause:** Tutor's audio plays through speakers → mic picks it up →
even with `echoCancellation: true`, some audio leaks through → Gemini hears
its own voice and fires `interrupted`. The `spoke: 0ms` confirms Gemini is
interrupting itself (no student speech preceded the interrupt).

**Fix (4 changes):**
1. **Audio gate suppresses during playback:** `shouldSendReal = vadSpeechActive && !tutorSpeaking`
   — never sends real audio while tutor is playing, only silence
2. **Echo guard in VAD callback:** requires tutor to have spoken for at least
   `MIN_TUTOR_SPOKE_FOR_BARGEIN_MS = 900` before a barge-in candidate is allowed
3. **`lastTutorAudioAt` tracking:** Records timestamp of each tutor audio chunk
   so echo guard knows when tutor last produced audio, even after playback ends
4. **Low-energy rejection:** candidate is dropped if mic dB is not clearly above
   ambient noise floor (fan hum doesn't pass)

**Key insight:** The `!tutorSpeaking` check in the audio gate is the primary
defense. Even if VAD detects the echo, the gate sends silence to Gemini, so
Gemini never hears it. The echo guard in `onVADSpeechStart` is a secondary
defense that prevents false barge-in UI events.

**Trade-off:** A real barge-in now needs ~220ms of sustained speech and enough
energy above ambient noise. This is slightly less "instant" than pure
`onSpeechStart`, but dramatically reduces false stops from fan noise.

### Issue 4: Stale Gemini Interrupt Events (`spoke_for=0ms`)
**Symptom:** `GEMINI INTERRUPTED` appears even when tutor is already silent.

**Root cause:** Gemini can emit late interruption notifications after a turn is
already complete; these are not real barge-ins.

**Fix:**
1. Backend ignores `interrupted` when `assistant_speaking` is false
2. Frontend ignores interruption events when no tutor audio is active
3. Reset `last_vad_bargein_at` on turn complete to avoid misleading latency

### Issue 5: Audio Tail Cut Off On `turn_complete`
**Symptom:** Transcript keeps receiving tutor words, but playback goes silent
mid-sentence.

**Root cause:** The frontend called `cancelPlayback()` as soon as it received
`turn_complete`. Since audio chunks can arrive faster than real-time playback,
there may still be buffered audio in `scheduledSources`; cancelling it truncates
the end of the utterance.

**Fix:**
1. On `turn_complete`, mark turn as pending and keep draining queued audio
2. Finalize the turn only when `scheduledSources.length === 0`
3. Keep `cancelPlayback()` for real interruptions only (barge-in/Gemini int)

### Issue 6: Student Audio Blocked During Playback Drain — "Not Hearing Me"
**Symptom:** Tutor finishes a response, student speaks, but tutor doesn't
respond or keeps talking non-stop as if nobody spoke.

**Root cause:** After `turn_complete`, the server stops sending audio but
the client has 40-100+ scheduled audio sources still draining (8+ seconds).
During this entire drain period, `tutorSpeaking=true`, so the audio gate
(`vadSpeechActive && !tutorSpeaking`) sends SILENCE to Gemini. The student's
actual speech never reaches Gemini.

**Evidence from logs:**
```
Session 1: student spoke at 08:30:11 — msSinceLastTutorAudio: 8238ms
  but tutorSpeaking: true, scheduledSources: 8 → gate sent silence
Session 2: 7 VAD barge-ins, 0 Gemini interruptions, 0 real student-heard events
  All "barge-ins" were from speaker echo, not student speech
```

**Fix (2 changes):**
1. **Audio gate no longer checks `!tutorSpeaking`:** Gate is now purely
   `vadSpeechActive` — when VAD confirms speech, real audio goes to Gemini
   regardless of playback state. Gemini's LOW sensitivity won't self-interrupt
   from echo (confirmed: 0 server-side interruptions in test sessions).
2. **Client-side barge-in only during drain period:** Added
   `SERVER_STREAM_GAP_MS = 800` — if server sent audio < 800ms ago (active
   streaming), skip client barge-in and let Gemini's server-side VAD handle it.
   During drain (server silent > 800ms), client barge-in with confirmation.

**Key insight:** The `!tutorSpeaking` gate was overly aggressive. It blocked
the student for the entire 8+ second drain period. Gemini with LOW sensitivity
handles echo fine — the gate only needs to filter fan noise (VAD's job).

### Issue 7: "Stop" Detected, But Tutor Keeps Talking
**Symptom:** Student says "stop"/"wait", `GEMINI_INT` appears in logs, but
the tutor audio keeps playing for seconds.

**Root cause (2 races):**
1. Frontend only called `cancelPlayback()` on Gemini interruption when VAD
   was disabled. With VAD enabled, interruption was treated as "confirmation"
   even when VAD had not canceled audio.
2. During the first ~800ms after `turn_complete`, speech was still classified
   as "active streaming", so client-side barge-in could be skipped despite
   buffered tutor audio still playing.

**Fix:**
1. Gemini interruption now **always** cancels local playback (`cancelPlayback`)
   and resets speaking state.
2. Drain detection now treats `turnCompletePending=true` as immediate drain
   period (no 800ms wait), allowing barge-in right away.
3. Barge-in telemetry now computes `spoke_for_ms` before cancellation and
   ignores barge-ins when playback is already silent.

---

## Configuration

### VAD Tuning
```
positiveSpeechThreshold: 0.72  // Much stricter than default 0.3
negativeSpeechThreshold: 0.45  // Avoids flapping around threshold
minSpeechMs: 260               // Filters quick noise bursts
redemptionMs: 700              // More stable speech segment boundaries
preSpeechPadMs: 0              // Detection-only mode, not capturing audio
model: "v5"                    // Newer, better discrimination
```

### Barge-in Confirmation + Noise Gate
```
BARGE_IN_CONFIRM_MS: 220        // Speech must persist before cancelPlayback()
MIN_TUTOR_SPOKE_FOR_BARGEIN_MS: 900
NOISE_CALIBRATION_MS: 1200      // Initial ambient floor measurement
VOICE_DB_MARGIN: +10dB          // Voice must exceed ambient by this margin
MIN_VOICE_DB: -45dB             // Absolute minimum voice loudness
```

### Grace Period
When VAD fires `onSpeechEnd`, audio keeps flowing for 500ms so Gemini
catches trailing syllables. Without this, last word gets cut off.

### Echo Guard + Server Streaming Detection
```
SERVER_STREAM_GAP_MS: 800  // If server sent audio < 800ms ago, "active streaming"
Audio gate:                // Now pure VAD: shouldSendReal = vadSpeechActive
                           // (removed !tutorSpeaking — was blocking student during drain)
Client barge-in:           // Only during drain (server silent > 800ms), not active streaming
lastTutorAudioAt:          // Tracks when server last sent audio for streaming detection
```

### Gemini Config
```
start_of_speech_sensitivity: LOW   // Client VAD gates audio, no need for aggressive detection
end_of_speech_sensitivity: LOW
prefix_padding_ms: 300
silence_duration_ms: 700           // Slightly longer to avoid premature turn-taking
```

---

## Metrics Dashboard (6 cards)
| Card | What it shows |
|---|---|
| VAD Barge-ins | Client-side interruptions detected |
| Gemini Ints | Server-side interrupted events |
| Turns | Complete tutor turns |
| VAD Avg (ms) | Average client-side cancel latency |
| Gemini Avg (ms) | Average Gemini interrupt latency |
| VAD Advantage | How much faster VAD is than Gemini |

---

## Test Scenarios
1. **Fan test:** Leave mic on with fan running, tutor should talk uninterrupted
2. **Voice barge-in:** Let tutor talk, say "wait" — should see VAD barge-in
3. **Topic change:** Tutor explains math, ask about German grammar
4. **Quick interruption:** Interrupt with just "hey" — does VAD catch it?
5. **VAD toggle A/B:** Disable VAD gate, see if fan noise causes false interrupts
6. **Trailing syllable test:** Say a full sentence — does Gemini hear the last word?

---

## Pending Improvements
- [ ] Tune `BARGE_IN_CONFIRM_MS` (200-300ms) for best balance of speed vs noise immunity
- [ ] Tune `VOICE_DB_MARGIN` for quieter voices in loud environments
- [ ] Test VAD accuracy: does threshold 0.72 catch soft speech reliably?
- [ ] Measure actual VAD advantage numbers with real usage
- [ ] Ring buffer: send last ~300ms of audio when VAD fires speechStart
      (so Gemini doesn't miss the first syllable)
- [ ] Test on mobile Safari (requires Safari 16+ for WASM SIMD)
- [ ] Consider gating video frames during silence too
