
Goal: Make grounding judge-visible, not just “we have RAG”.
Must be perfect:

Deterministic retrieval (same query → stable top results)

Source snippet shown in UI (small citation card) + spoken “I’m using your curriculum”

Tool contract: get_grounded_curriculum(query, student_id, topic_id) -> {snippets[], source_id, confidence}

Failure behavior: “I can’t find that in your materials; want me to search the web?” (and default to NO)

Why: judges explicitly care about hallucination avoidance / robustness.

Good. Vertex AI RAG Engine is a solid choice for **judge-visible grounding**, but it has sharp edges.

## Caveats / risks (early)

* **Latency**: retrieval can add noticeable delay; if you call RAG too often you’ll kill the “live” feel.
* **Stability**: top results can shift if chunking/embedding settings change.
* **Demo failure mode**: “no relevant results” must be handled cleanly, or you’ll hallucinate or stall.

---

# The PoCs you MUST build for Vertex AI RAG Engine (and make perfect)

## 05_rag_grounding_proof (mandatory)

**Goal:** Make grounding obvious to judges in 2 seconds.

**Perfect means:**

* Tool call is explicit: `get_grounded_curriculum(query, student_id, topic_id, k=3)`
* Returns: `snippets[]` with `{text, source_title, page, uri, confidence}`
* UI shows a small “📚 From curriculum” card with **1–2 snippets** and the source.
* Voice: one line like: “I’m using your workbook, page 12.”

**Hard rule:**
If confidence < threshold OR empty results → tutor must say:
“I can’t find that in your materials. Want me to use web search?” (default **no**)

---

## 05b_rag_index_quality (mandatory)

**Goal:** Ensure your RAG corpus is *actually usable*.

**Perfect means:**

* Chunking strategy validated for your content (math formulas vs language rules differ)
* Metadata is present and correct: `doc_title`, `page_number`, `topic`, `language`, `grade`
* A tiny eval set: 20 queries → expected doc/page → pass rate tracked

**Deliverable:** `rag_eval.json` + a one-page results table.

---

## 05c_rag_latency_budget (mandatory)

**Goal:** Keep “live” perception while using RAG.

**Perfect means:**

* Time each retrieval end-to-end (request → snippets available)
* Budget target: **< 700ms median**, **< 1200ms p95** during demo
* Caching:

  * cache by `(student_id, topic_id, normalized_query)` for the session
  * preload 3–5 “most likely” snippets at session start per topic (optional)

---

## 05d_rag_fallback_modes (mandatory)

**Goal:** No stalls, no hallucinations.

**Perfect means:**

* If RAG slow: tutor continues with **a guiding question** while waiting
* If RAG empty: tutor switches to **Socratic + ask for clarification**, not facts
* If RAG returns conflicting snippets: tutor surfaces both, asks student which matches the worksheet

---

# How to integrate RAG without killing your Live loop

### Rule 1: RAG only for “teaching facts”

Examples:

* grammar rules, definitions, formulas, exceptions

### Rule 2: No RAG for “coaching turns”

Examples:

* “show me your work”, “what did you do here?”, “try this next step”

### Rule 3: Speak-first, fetch-second

While RAG runs, tutor does:

* 1 short observation
* 1 question to unblock
  Then uses retrieved snippet to confirm.

---

# Vertex AI RAG Engine setup decisions you should lock (or you’ll churn)

## Corpus strategy

* **One corpus per domain** (German, Math, Chemistry) OR one corpus with strong metadata filters.
* For demo reliability: **use metadata filters** (topic + language + grade).

## Grounding contract (your tool)

Your tool should accept:

* `query`
* `filters` (topic, language, grade)
* `k`
  and return:
* `snippets`
* `confidence`
* `source metadata`

This is what prevents random retrieval and makes it stable.

---

# Minimum “judge-visible” UI pattern

Add a compact panel near the whiteboard:

* Title: “📚 Grounded Source”
* Shows 1 snippet + (doc title + page)
* Clicking expands (optional)

Do NOT dump long citations. One clean snippet wins.

---

# PoC list update (final)

Keep your existing 4. Add these:

* **05_rag_grounding_proof**
* **05b_rag_index_quality**
* **05c_rag_latency_budget**
* **05d_rag_fallback_modes**
* **06_session_resilience** (don’t skip)
* **07_latency_instrumentation** (don’t skip)
* **08_tool_action_moment** (pick ONE)

That’s it. More PoCs after this is likely avoidance.

§§§§§§

📚 05_rag_grounding_proof
Goal

Judges SEE grounding.

Must-haves

Visible snippet card:

source + page + short text

Tutor explicitly says:

“Using your workbook…”

Deterministic retrieval (same query → same result)

Tool contract stable

Failure = lose

Hidden RAG (judges don’t see it)

Long irrelevant chunks

🧪 05b_rag_index_quality
Goal

RAG actually works.

Must-haves

Clean chunking (no broken sentences/formulas)

Metadata filters:

topic

language

level

20 test queries:

≥80% correct doc retrieved

No garbage chunks

Failure = lose

Wrong sources → hallucination risk

⚡ 05c_rag_latency_budget
Goal

RAG doesn’t break “live”

Must-haves

Median < 700ms

p95 < 1200ms

Async call (non-blocking)

Tutor speaks while waiting

Failure = lose

pauses → feels fake

🛟 05d_rag_fallback_modes
Goal

Never stuck, never hallucinate.

Must-haves

Empty result → clear response:

“I can’t find that in your material”

Slow → continue coaching without it

Conflicting → show 2 options + ask user

Failure = lose

Silence

Guessing
