If you can make it flawless and it improves demo clarity (worksheet readability), do it. Otherwise it’s extra failure surface
---

Goal

Increase readability + reduce vision ambiguity without adding fragility.

Must-haves (perfection)

Single control to switch input mode: Camera / Screen

Switching is instant (< 500ms perceived) and does not reset session

Tutor acknowledges switch in 1 line:

“Ok, I’m looking at your screen now.”

Privacy UX:

clear “LIVE” indicator + what is being shared

quick “Stop sharing” voice command works

Vision pipeline parity:

same “proactive vision” behavior works on both inputs

same “camera unclear” logic becomes “scroll/zoom” prompts

Input quality:

720p or at least readable text

throttled capture rate (e.g., 1–2 FPS) to protect latency

Failure handling:

if screen share denied → fallback to camera with clear message

if capture fails → keep audio live; show banner

Pass/fail tests

✅ Switch modes 5 times in a row without reconnect or broken audio

✅ Tutor correctly references on-screen element within 2 turns

✅ “Stop sharing” ends share immediately and continues voice-only

✅ Permission denied does not crash; returns to prior mode

✅ No noticeable latency spikes after switching (your HUD should confirm)

Failure = lose

Switching breaks the websocket/audio

User must refresh

Screen share leaks unclear state (“is it on?”)
