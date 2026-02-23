Goal: WebSocket drop ≠ dead demo.
Must be perfect:

Auto-reconnect with backoff + resume state (student profile, topic, last whiteboard notes)

Clear UI status (“Reconnecting… voice continues” or “Paused”)

If resume fails: graceful fallback to “restart session” with preserved student state.

Why: live demos die from disconnects
-----

Goal

Demo cannot die.

Must-haves

Auto reconnect (≤ 2s)

Resume:

student

topic

whiteboard

Clear UI state:

reconnecting / resumed

Retry logic (≥3 attempts)

Failure = lose

Refresh needed → demo over
