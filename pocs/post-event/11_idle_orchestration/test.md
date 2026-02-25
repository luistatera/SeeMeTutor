Verification

 1. Run backend locally: cd pocs/11_idle_orchestration && uvicorn main:app --reload --port 9100
 2. Open <http://localhost:9100> in browser
 3. Test each PRD criterion:

- M1: Start mic, stay silent. Gentle check-in at ~10s, offer options at ~25s
- M2: After gentle check, no more prompts until 25s. After options, no more until 90s
- M3: Start speaking during any idle stage. Badge turns green (Active), timers reset
- M4: Enter away mode (button or "give me a moment"). Wait 2+ minutes. Zero tutor speech
- M5: Return from away ("I'm back" or button). Tutor recaps in 1 line, continues
- M6: Say "give me a moment" -> badge turns gray. Say "I'm back" -> badge turns blue then green
- M7: Watch idle state badge through full cycle: Active (green) -> Waiting (yellow) -> Away (gray) -> Resuming (blue) -> Active (green)
- M8: Start speaking right as a check-in fires. Agent stops, no overlap

 4. Check event log panel for idle state transitions:
    - IDLE STATE -> waiting (detail: gentle_check)
    - IDLE STATE -> waiting (detail: offer_options)
    - IDLE STATE -> away
    - IDLE STATE -> resuming
    - IDLE STATE -> active

 5. Check silence timer counts up during silence and resets on speech

 6. Check manual buttons:
    - "Take a Break" button -> immediate away mode
    - "I'm Back" button -> resuming then active

 7. Verify no internal control text leaks in tutor transcript
