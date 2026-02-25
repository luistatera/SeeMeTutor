Goal

Silence feels human, not awkward; the agent manages attention correctly.

Must-haves (perfection)

Server-driven (not prompt-dependent) idle state machine:

10s: gentle check-in

25s: offer options (repeat / hint / pause)

90s: enter away mode (stop talking, wait)

Tone control:

no nagging

short, calm, 1 sentence max per check-in

Interrupt-safe:

if user speaks, idle timers stop instantly

agent does not “finish” an idle prompt over the user

Context-aware triggers:

don’t interrupt if student is actively writing (if you can detect via motion/changes in frame, optional)

do interrupt if user said “give me a moment” → explicitly switch to away mode

Voice commands integrated:

“give me a moment” → away mode immediately

“I’m back” → resume and recap last step in 1 line

UI state visible:

“Waiting”, “Away”, “Resuming”

No runaway loops:

maximum 1 prompt per stage, then silence

Pass/fail tests

✅ Leave mic on and do nothing: check-ins happen exactly at thresholds

✅ User starts speaking during check-in: agent stops within 200ms

✅ Away mode: agent stays quiet indefinitely (no random chatter)

✅ Resume: agent gives a 1-line recap + next step

✅ Works with all profiles/languages (no language mixing in idle prompts)

Failure = lose

Agent nags repeatedly

Agent talks during away mode

Silent periods feel buggy or “stuck”

Idle prompt overlaps user speech
