# POC 03 - Multilingual Pedagogy Test Guide

## Run

```bash
cd pocs/03_multilingual
uvicorn main:app --reload --port 8300
```

Open `http://localhost:8300`.

## Priority Test Cases

1. Strict immersion (M1)
- Select `Luis (DE Immersion)`.
- Ask in English: "Can you explain this in English?"
- Pass if tutor responds only in German for at least 10 turns.
- Check metrics: `Purity Rate` near 100%, `Mixed Turns` stays 0.

2. Guided bilingual flow (M2 + M3)
- Select `Mode Override = Guided Bilingual`.
- Ask a grammar concept question (for example: "What is dative case?").
- Pass if tutor explains in L1 first, then switches to L2 practice in the next turn, without mixing languages in a single response.
- Check `Guided Adherence` increases and `Mixed Turns` remains 0.

3. Confusion fallback (M4)
- In `Luis` profile, trigger confusion twice (for example: "I don't get it" then "I'm still confused").
- Pass if tutor switches to L1 fallback promptly.
- Check `Fallback Triggers` increments and `Fallback Lat (turns)` remains low (target < 1 turn average).

4. Profile-specific behavior (M5)
- Run three short sessions:
  - `Luis`: should default to DE immersion.
  - `Daughter`: should default to PT immersion.
  - `Wife`: should default to auto with PT preference.
- Pass if each session loads the expected mode and contract in the contract panel.

5. Auto switching (S2)
- Select `Wife` profile.
- Speak first in Portuguese, then in English.
- Pass if tutor adapts language with you across turns.
- Check `Language Flips` increases (>0) in this mode.

6. Recap interval (S1)
- Stay in immersion and keep interaction in L2 for multiple turns.
- Pass if backend eventually triggers an L1 recap turn.
- Check `Recap Triggers` increments.

## Metrics to Monitor

- `Purity Rate`: single-language tutor turns / total tutor turns.
- `Mixed Turns`: turns flagged as mixed language.
- `Guided Adherence`: guided-mode turns that matched expected explain/practice language.
- `Fallback Triggers`: confusion fallback activations.
- `Fallback Lat (turns)`: average turns to land fallback language.
- `Language Flips`: count of tutor language changes between turns.
- `L2 Ratio`: L2 words / (L1+L2 words).
- `Confusion Signals`: detected confusion utterances.
- `Recap Triggers`: L1 recap activations after L2 streak.

## Logs

Session artifacts are written to `pocs/03_multilingual/logs/`:
- `{timestamp}_poc3-*.jsonl` - raw machine log
- `{timestamp}_poc3-*_details.log` - readable event log
- `{timestamp}_poc3-*_transcript.log` - readable transcript
- `details.log` / `transcript.log` - appended rollups

Important JSONL events to inspect:
- `internal_control_sent`
- `confusion_signal`
- `fallback_triggered`
- `turn_language_eval`
- `session_end`
