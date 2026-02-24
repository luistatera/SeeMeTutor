Verification

 1. Run backend locally:
    ```
    cd pocs/05_search_grounding && uvicorn main:app --reload --port 8500
    ```
 2. Open <http://localhost:8500> in browser
 3. Click **Start Session** (grants mic + camera permissions)
 4. Test each PRD criterion:

### M1: Google Search tool enabled
- Confirm in backend logs: Gemini session connects without errors
- The config includes `tools=[Tool(google_search=GoogleSearch())]`

### M2: Grounding metadata parsed
- Ask a factual question: "What is the atomic number of carbon?"
- Check terminal logs for `GROUNDING #1:` with snippet and source
- Check for `grounding_citation` in the JSONL session log under `logs/`

### M3: Citation card renders with toast lifecycle
- Ask a factual question (e.g., "What's the quadratic formula?")
- Citation toast appears at bottom-right of screen
- Toast fades in (~300ms animation)
- Toast holds for ~8 seconds
- Toast fades out (~500ms)
- Citation card also appears in the right-side "Grounding Citations" panel with timestamp

### M4: "Verifying..." indicator
- Watch the bottom-right corner when asking a factual question
- Purple spinner with "Verifying..." text should briefly appear before the citation toast replaces it

### M5: Tutor speaks grounded facts naturally
- Listen to the tutor's response after a factual question
- PASS: "Let me check that... yes, the formula is pi times r squared"
- PASS: "That's correct — the atomic number of carbon is 6"
- FAIL: "According to my Google Search results, source 1 says..."
- FAIL: "I found 3 results. Result 1 from Wikipedia states..."

### M6: Tutor skips search for coaching turns
- Say: "Can you explain your thinking?"
- Say: "What did you try first?"
- Say: "Show me your work"
- Say: "Let's break this into smaller steps"
- For all of these: **no** grounding event, **no** citation card
- Confirm "Search Events" metric in the dashboard does NOT increase

### M7: Slow/empty search handled gracefully
- Ask an obscure or nonsensical factual question (e.g., "What's the melting point of imaginium?")
- Tutor should NOT stall or go silent
- Expected: tutor says something like "I'm not fully sure — let's reason through it together"
- No gap > 3 seconds of silence caused by search latency

### M8: Multilingual search (PT/DE/EN)
- English: "What's the capital of Brazil?" → citation appears, tutor responds in English
- Portuguese: "Qual e a formula da velocidade?" → citation appears, tutor responds in Portuguese
- German: "Was ist die chemische Formel von Wasser?" → citation appears, tutor responds in German

### Full demo script (run 3 times)
1. Ask: "What's the formula for the area of a circle?"
   - Citation card appears, tutor speaks the fact naturally
2. Ask: "What's the capital of Brazil?"
   - Citation card appears, tutor responds correctly
3. Say: "I don't understand this step"
   - Tutor coaches directly — NO search, NO citation card
4. Alternate factual and coaching questions — grounding fires only on factual turns

### Metrics check
- After a session with mixed factual and coaching questions:
  - "Search Events" count matches the number of factual questions where search triggered
  - "Citations Shown" count matches the number of toast/card appearances
  - "Turns" count reflects total conversation turns
  - Backend final metrics log shows `grounding_events`, `citations_sent`, and `search_queries`

 5. Check browser console for grounding-related logs confirming: WS connected,
    grounding message received, citation card rendered
 6. Check backend terminal for `GROUNDING #N` log lines with snippet and source details
