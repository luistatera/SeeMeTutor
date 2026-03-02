# Prompt Management Analysis

This document analyzes the current state of prompt management in the SeeMeTutor project and compares it against Google's recommended best practices for the Gemini API.

## Current Implementation

Prompts in this project are primarily managed within the Python source code.

*   **Location:** The main tutor system prompt is located in [backend/agent.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/agent.py).
*   **Structure:** It is divided into distinct string variables (`_BASE_INSTRUCTION`, `_PHASE_GREETING`, `_PHASE_CAPTURE`, `_PHASE_TUTORING`, `_PHASE_REVIEW`).
*   **Assembly:** These variables are concatenated into a single, massive `SYSTEM_PROMPT` string at runtime.
*   **Context Window:** The system instruction handles all phases at once, plus general rules.

**What's working well:**
*   **Structure:** The use of Markdown headers (like `## Safety and Scope`) aligns perfectly with Gemini best practices for separating instructions.
*   **Clarity:** The rules are explicit and use strong constraints (e.g., "Rule 1: NEVER GIVE DIRECT ANSWERS").
*   **Modularity in Code:** Breaking the prompt into variables based on phases (`_PHASE_GREETING`, etc.) makes it easier for a developer to read and maintain the prompt within the IDE.

**Areas for improvement:**
*   **Hard-coded Prompts:** The prompts are coupled with the application logic. To fix a typo in the prompt or adjust a behavioral rule, you must modify the backend code and potentially redeploy the service.
*   **Testing Friendliness:** Because the prompt is buried in Python code, non-developers (like prompt engineers or content specialists) cannot easily iterate on it using tools like Google AI Studio.
*   **Length & Token Usage:** The prompt is sent in its entirety for every interaction. While Gemini handles large contexts well, sending instructions for all phases when the user is only in one phase (e.g., sending `_PHASE_REVIEW` instructions during `_PHASE_CAPTURE`) consumes unnecessary tokens and might cause the model to cross-contaminate instructions.

## Google/Gemini Best Practices

Google's official guidelines for managing prompts and system instructions recommend the following:

1.  **Centralized and Version-Controlled Storage:**
    Avoid hard-coding prompts directly into your application. Instead, store them in a centralized, version-controlled location such as JSON/YAML files, Cloud Storage, or specialized prompt registries. This completely decouples prompt iteration from code deployment.
2.  **Iterative Refinement & Testing:**
    Prompt engineering is an iterative process. Keep prompts in a format that can be easily loaded into evaluation tools or AI Studio to test edge cases without requiring a backend development environment.
3.  **Context Caching for Long Prompts:**
    For large, static system instructions, utilize **Context Caching**. This significantly reduces costs and latency for subsequent requests using the same detailed instructions.
4.  **PTCF Framework:**
    Structure prompts using Persona, Task, Context, and Format. The current prompt does this well, but it could be formalized further.
5.  **Role/Phase Separation:**
    If a system has distinct phases, consider dynamically injecting only the relevant phase instructions into the prompt (or system instructions) instead of appending all phases at once. Alternatively, if using a large context, ensure instructions are clearly anchored.

## Recommendations

Based on the analysis, here are the recommendations for improving prompt management in SeeMeTutor:

1.  **Extract Prompts to Configuration Files (High Priority)**
    Move `_BASE_INSTRUCTION` and the phase instructions out of [backend/agent.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/agent.py) and into external files (e.g., `prompts/system_instruction.md`, `prompts/phases/greeting.md`). The backend can read these files at startup. This enables prompt engineers to tweak the personality without touching Python code.
2.  **Dynamic Phase Loading (Medium Priority)**
    Instead of concatenating all phases into one massive `SYSTEM_PROMPT`, configure the ADK Agent (or underlying Gemini call) to only include the `_BASE_INSTRUCTION` and the **current** phase's instructions. When the agent calls [set_session_phase](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/agent.py#661-773), update the system instructions for the next turn. This reduces token usage and prevents the model from accidentally applying `_PHASE_REVIEW` logic during `_PHASE_TUTORING`.
3.  **Implement Context Caching (Medium Priority)**
    If dynamic phase loading is not preferred and you want to keep the massive, all-in-one system prompt, implement Gemini **Context Caching** on the combined `SYSTEM_PROMPT`. This will reduce latency and cost for long-running tutor sessions since the system instruction won't need to be fully reprocessed on every turn.
4.  **Create a Prompt Testing Library (Low Priority)**
    Establish a separate test script or suite that loads these external prompt files and runs them against a predefined set of tricky student inputs (e.g., "tell me the answer", "ignore previous instructions") to automatically evaluate prompt regressions.
