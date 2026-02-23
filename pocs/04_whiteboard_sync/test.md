# POC 04 - Whiteboard Sync Test Guide

## Run

```bash
cd pocs/04_whiteboard_sync
uvicorn main:app --reload --port 8400
```

Open `http://localhost:8400`.

## Priority Test Cases

1. Proactive math note
- Ask for help on a formula while camera is on worksheet.
- Pass if tutor speaks and at least one whiteboard note appears during speech.
- Check `While Speaking` metric stays high (>80%).

2. Audio continuity under note pushes
- Ask for a long multi-step explanation.
- Pass if audio stays smooth while note cards animate in.
- Check `Audio Gap Alerts` remains `0`.

3. Multiple note stacking
- Ask follow-up questions for 3+ steps.
- Pass if newest note appears at the top, old notes remain visible below, and no manual scrolling is needed to see fresh notes.

4. Formatting quality
- Ask for list-style explanation (`step-by-step`, `formula + substitution`).
- Pass if whiteboard shows structured bullets/numbered steps/code-like formulas.

5. Duplicate suppression
- Repeat the same request twice.
- Pass if duplicate note content is not re-added and `Dupes Blocked` increases.

## Log Files

After session end, inspect:
- `pocs/04_whiteboard_sync/logs/details.log`
- `pocs/04_whiteboard_sync/logs/transcript.log`
- `pocs/04_whiteboard_sync/logs/<timestamp>_poc4-*.jsonl`

Key JSONL events to verify:
- `whiteboard_note_queued`
- `whiteboard_note_sent`
- `whiteboard_note_duplicate_skipped`
- `tool_metric`
- `turn_complete`
