# Context Caching RAG Plan for SeeMe Tutor

## Objective

Implement a low-latency, real-time "RAG" (Retrieval-Augmented Generation) system for the SeeMe Tutor backend.

Given the strict core promise of the app—**immediate interruption handling (true barge-in)** and **low latency**—traditional RAG approaches (like Vertex AI Search or unofficial NotebookLM APIs) that introduce a database retrieval hop are **not recommended**.

Instead, we will use **Gemini Context Caching** via the Gemini Live API. By uploading the curriculum and keeping it "warm" in the model's memory, we achieve zero retrieval latency, preserving the lightning-fast performance required for voice interactions.

---

## 1. Prerequisites and Setup

* **Dependencies**: Ensure the `google-genai` package is up to date in `backend/requirements.txt` to support the latest File API and Context Caching features.
* **Curriculum Data**: Gather the tutoring material (textbooks, PDFs, markdown lesson plans, vocabulary lists) that the tutor needs to reference.

## 2. Implementation Steps

### Step 2.1: Upload Curriculum Files

Create a utility script (e.g., `backend/upload_curriculum.py`) to upload the reference materials to Google's servers using the Gemini File API.

* **Action**: Use `client.files.upload(file="path/to/material.pdf")`.
* **Output**: Save the resulting `file.name` (the URI) to a configuration file or Firestore so the main backend can reference it.

### Step 2.2: Create the Context Cache

Before a session starts (or globally on backend startup), create a Context Cache connecting the uploaded files to the specific model (e.g., `gemini-2.5-flash` or `gemini-3.0-pro`).

* **Action**: Use `client.caches.create(...)` passing the `file.name` in the contents and setting a Time-To-Live (TTL).
* **Output**: Retrieve the `cache.name` (the cache identifier).

### Step 2.3: Integrate with Gemini Live WebSocket

Modify the main WebSocket bridge in `backend/gemini_live.py`.

* **Action**: When initializing the Live API connection, instead of (or in addition to) sending a massive system instruction block, initialize the session with the `cache_name`.
* **Result**: The model will instantly have access to the entire curriculum for generating responses without fetching from an external database.

## 3. Risks and Mitigations

* **Cache Eviction / TTL**: Context caches expire.
  * *Mitigation*: Implement a check in `main.py` to ensure the cache is active before bridging the connection. Recreate the cache if it has expired.
* **Cost**: Caching large documents incurs an hourly storage cost.
  * *Mitigation*: Only cache the specific curriculum needed for the current demos, rather than the entire universe of documents. Use Flash models where possible.
* **Dynamic Updates**: If the student's progress updates in Firestore, updating a fixed cache is slow.
  * *Mitigation*: Keep the *static* curriculum in the Context Cache. Pass the *dynamic* student progress (e.g., "Mastered German A2 Module 1") in the real-time system instructions or as a normal message at the start of the WebSocket session.

## 4. Evaluation Criteria for Demo

* **Latency Check**: Voice response latency (Time to First Audio Byte - TTFAB) must remain identical to the baseline with no context.
* **Accuracy**: Ask the tutor a specific question from the cached curriculum to ensure it is accurately retrieving and grounding its response in the provided material.
* **Barge-in**: Ensure the user can interrupt the tutor while it is explaining a concept from the curriculum.
