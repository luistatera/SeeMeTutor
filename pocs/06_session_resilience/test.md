Verification

 1. Run backend locally: cd pocs/06_session_resilience && uvicorn main:app --reload --port 8600
 2. Open <http://localhost:8600> in browser
 3. Test each PRD criterion:

- M1: Click "Simulate Disconnect" during active session -> WS reconnects within 2s, audio resumes
- M2: Observe backend logs during reconnect -> Gemini session re-established with context injection
- M3: After reconnect, tutor does NOT re-introduce itself or ask "what shall we work on?"
- M4: Check backend logs -> retry delays follow exponential backoff (~500ms, ~1s, ~2s)
- M5: During disconnect, banner shows "Reconnecting..." then "Reconnected!" on success
- M6: Block network for >10s -> after 3 failed retries, UI shows "Session ended — please restart"
- M7: Run a long session -> Gemini 1011 close code produces clean session-end message, not crash

 4. Check browser console for reconnect logs: WS close reason, retry count, state payload sent
 5. Check backend logs directory for JSONL entries: reconnect_attempt, reconnect_success, context_injected events
