# PoC 05 — Google Search Grounding

## Why Google Search instead of RAG

- **Time:** RAG requires corpus building, chunking, embeddings, eval — weeks of work
- **Reliability:** Google Search never returns empty; a half-built RAG pipeline can fail live
- **Judge alignment:** Rubric says "hallucination avoidance and grounding evidence" — not "RAG"
- **Google-native:** Google Search grounding is a first-party Gemini feature — judges love this
- **V2 story:** RAG with curriculum materials is the obvious next step. Mention it in the roadmap

---

## Goal

The tutor **fact-checks itself in real time** using Google Search before teaching facts.
Judges must SEE grounding happening — it cannot be invisible.

---

## What "perfect" looks like

### 1. Tutor uses Google Search grounding automatically

- When the student asks about a fact (formula, grammar rule, definition, historical date), the tutor verifies via Search before answering
- The tutor NEVER guesses. If unsure, it searches. If Search fails, it says so
- Config: enable `google_search` tool in the Gemini Live API session config

### 2. Grounding is visible in the UI (judge must see it in 2 seconds)

A small citation card appears when the tutor references a searched fact:

```
+--------------------------------------+
| Verified source                      |
| "The quadratic formula is..."        |
| — Khan Academy                       |
+--------------------------------------+
```

- Shows: **1 snippet** + **source name/domain**
- Appears near the whiteboard/chat area, not in a separate panel
- Fades after 8–10 seconds (doesn't clutter the screen)
- Clean, minimal — one source wins. Never dump multiple citations

### 3. Tutor speaks the grounding naturally

The tutor weaves verification into speech without being robotic:

**Good:**
- "Let me check that... yes, the formula for acceleration is force divided by mass."
- "That's correct — the past tense of 'gehen' is 'ging'."
- "Actually, let me verify... the atomic number of carbon is 6, not 8."

**Bad:**
- "According to my Google Search results, source 1 says..."
- "I found 3 results. Result 1 from Wikipedia states..."
- Any robotic citation-reading

### 4. Grounding only for facts, never for coaching

**Search triggers (teaching facts):**
- Formulas, definitions, grammar rules, chemical properties
- Historical dates, scientific constants, vocabulary translations
- Anything the student could look up in a textbook

**No search (coaching turns):**
- "Show me your work"
- "What did you try first?"
- "Can you explain your thinking?"
- "Let's break this into smaller steps"

The system prompt must enforce this distinction.

### 5. Failure handling is graceful

| Scenario | Tutor behavior |
|---|---|
| Search returns results | Use them, show citation card, speak naturally |
| Search is slow (> 2s) | Continue coaching with a question while waiting |
| Search returns nothing relevant | "I'm not 100% sure about that — let's work through it together step by step" |
| Student asks opinion (no fact to verify) | Skip search entirely, coach directly |

**Hard rule:** The tutor NEVER stalls or goes silent. If Search is slow or empty, the Socratic method fills the gap.

---

## Implementation spec

### Gemini config

```python
from google.genai import types

config = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    system_instruction=SYSTEM_PROMPT,
    tools=[types.Tool(google_search=types.GoogleSearch())],
)
```

### System prompt addition (append to existing tutor prompt)

```
## Grounding rules

You have access to Google Search. Use it to verify facts before teaching them.

When to search:
- Student asks about a formula, rule, definition, or factual claim
- You need to confirm something before correcting the student
- You are not 100% certain about a specific fact

When NOT to search:
- You are asking the student a guiding question
- You are encouraging them or giving process guidance
- The conversation is about their approach, not about facts

After searching:
- Weave the verified fact into your response naturally
- Never read out citations robotically
- If search returns nothing useful, say "I'm not fully sure — let's reason through it together"
- Never guess or fabricate facts
```

### WebSocket message for citation card

When the backend detects a grounding metadata block in the Gemini response:

```json
{
  "type": "grounding",
  "data": {
    "snippet": "The quadratic formula: x = (-b +/- sqrt(b^2 - 4ac)) / 2a",
    "source": "Khan Academy",
    "url": "https://khanacademy.org/..."
  }
}
```

Frontend renders the citation card, auto-dismisses after 8–10 seconds.

### Parsing grounding metadata from Gemini response

The Gemini API returns grounding metadata when Search is used. Extract it:

```python
# In the Gemini response handler
for part in response.server_content.model_turn.parts:
    if hasattr(part, 'text') and part.text:
        # Send text/audio as usual
        pass

# Check for grounding metadata on the response
if hasattr(response, 'grounding_metadata'):
    meta = response.grounding_metadata
    if meta.search_entry_point:
        # Extract the top source
        for chunk in meta.grounding_chunks:
            if chunk.web:
                citation = {
                    "type": "grounding",
                    "data": {
                        "snippet": chunk.web.title,
                        "source": chunk.web.uri,
                    }
                }
                await ws.send_json(citation)
                break  # Only show the top source
```

---

## Frontend UI spec

### Citation card

- Position: bottom-right of the main area, overlaying slightly
- Style: subtle background (semi-transparent), small font, rounded corners
- Animation: fade in 300ms, hold 8s, fade out 500ms
- Max width: 320px
- Content: snippet text (max 2 lines, truncated) + source domain
- No click-to-expand needed for V1 — keep it simple

### Visual feedback integration

When a search is triggered, briefly show a subtle "Verifying..." indicator near the tutor's avatar/status area. This tells the student (and judges) the tutor is checking its facts.

---

## What this PoC must prove

1. Google Search grounding works with the Gemini Live API (audio modality)
2. Grounding metadata is parseable and forwardable to the frontend via WebSocket
3. The citation card renders cleanly without disrupting the tutoring flow
4. The tutor naturally integrates verified facts into spoken responses
5. Failure modes (slow search, no results) are handled without stalling

---

## Demo script for this PoC

1. Student asks: "What's the formula for the area of a circle?"
2. Tutor searches, citation card appears ("Area = pi * r^2 — Math is Fun")
3. Tutor says: "Right, the area of a circle is pi times the radius squared. So what's the radius in your problem?"
4. Student asks: "What's the capital of Brazil?"
5. Tutor searches, card appears ("Brasilia — Wikipedia")
6. Tutor says: "It's Brasilia — a lot of people think it's Rio or Sao Paulo. Did you have a geography question?"
7. Student says: "I don't understand this step"
8. Tutor does NOT search — coaches directly: "Which part is confusing? Show me where you got stuck."

---

## Success criteria

- [ ] `google_search` tool enabled in Gemini Live API config
- [ ] Grounding metadata parsed from Gemini responses
- [ ] Citation card renders in frontend (fade in/out, clean design)
- [ ] "Verifying..." indicator shows briefly during search
- [ ] Tutor speaks grounded facts naturally (not robotic citations)
- [ ] Tutor skips search for coaching turns
- [ ] Slow/empty search handled gracefully (no stalls, no silence)
- [ ] Works in all 3 languages (PT/DE/EN) — search adapts to language context
- [ ] End-to-end demo script passes cleanly 3 times in a row
