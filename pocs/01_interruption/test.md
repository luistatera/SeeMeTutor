Verification

 1. Run backend locally: cd backend && uvicorn main:app --reload --port 8000
 2. Open <http://localhost:8000> in browser
 3. Test each PRD criterion:

- M1: Fan running → 0 false stops during tutor response
- M2: Say "wait" during tutor → playback stops quickly
- M3: 5 tutor turns on speakers → 0 self-interruptions
- M4: Tutor finishes → speak within 2s → tutor responds
- M5: Listen for last word → no mid-word cuts
- M6: All above without headphones

 1. Check browser console for [VAD] logs confirming: init, speech start/end, noise floor
 lock, barge-in confirmations
