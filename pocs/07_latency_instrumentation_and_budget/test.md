# POC 07 — Latency Instrumentation & Budget Test Guide

## Run

```bash
cd pocs/07_latency_instrumentation_and_budget
uvicorn main:app --reload --port 8700
```

Open `http://localhost:8700`.

## Priority Test Cases

1. Response Start Latency appears
- Click Start Mic and say "Hello, what can you help me with?"
- Wait for tutor to respond.
- Pass if the Latency HUD shows a Response Start value in milliseconds (should be green, < 500ms typical).
- Check server logs for `latency_event` with `metric: response_start`.

2. Interruption Stop Latency appears
- Let the tutor give a long answer.
- Interrupt mid-speech by saying something loud and clear.
- Pass if the HUD shows an Interruption value. Check that the metric card updates.
- Server logs should show `latency_event` with `metric: interruption_stop`.

3. First Byte Latency (one-time)
- Start a fresh session and click Start Mic.
- Pass if the HUD shows a First Byte value after the tutor speaks the first time.
- This value should not change for the rest of the session.

4. Turn-to-Turn Gap
- Complete a full exchange (student speaks, tutor responds, student speaks again).
- Pass if the Turn Gap row in the HUD shows a value after the second student turn.

5. Threshold alerts
- Ask a complex multi-part question that may slow down the response.
- If response start exceeds 800ms, the HUD row should flash red and the Alerts metric card should increment.
- Check the event log for a red-highlighted alert entry.
- Server logs should show `latency_alert` events.

6. Latency report on turn_complete
- Complete 3+ turns of conversation.
- Pass if after each tutor response, the HUD updates with new avg and p95 values.
- Verify the metric cards show running averages.

7. Session summary export
- Click the "Summary" button (or end the session).
- Pass if a summary table/overlay appears showing all metrics with min/avg/max/p95.
- This data should be copy-pasteable for documentation.

8. Color coding accuracy
- Over multiple turns, verify:
  - Green (< 500ms response start, < 200ms interrupt)
  - Yellow (500-800ms response start, 200-400ms interrupt)
  - Red (> 800ms response start, > 400ms interrupt)

9. HUD toggle
- Click the HUD toggle button.
- Pass if the latency overlay hides/shows without disrupting audio or the session.

## Log Files

After session end, inspect:
- `pocs/07_latency_instrumentation_and_budget/logs/details.log`
- `pocs/07_latency_instrumentation_and_budget/logs/transcript.log`
- `pocs/07_latency_instrumentation_and_budget/logs/<timestamp>_poc7-*.jsonl`

Key JSONL events to verify:
- `latency_event` (with metric name, value_ms, stats)
- `latency_alert` (with metric name, value_ms, threshold_ms)
- `latency_report` (full stats snapshot on turn_complete)
- `session_latency_summary` (final summary at session end)
- `turn_complete`
- `gemini_interrupted`
