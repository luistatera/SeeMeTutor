# Organize PoCs and Integrate into Main App

## 1. Analysis of Current State (What Needs Improvement)

Currently, the `pocs/` directory contains folders for each Proof of Concept (PoC). Each of these folders has a complete, standalone copy of [index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/pocs/01_interruption/index.html) and [main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py) (and potentially other backend files).

**Issues with this approach:**
- **Code Duplication:** The 1500-line [main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py) and 3000-line [index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/pocs/01_interruption/index.html) are duplicated across 10+ PoCs.
- **Entangled Logic:** The specific new logic for a PoC (e.g., VAD integration for PoC 01, Idle Orchestrator logic for PoC 02) is mixed into the massive monolithic files, making it incredibly hard to see exactly what changed.
- **Merge Hell:** Integrating "the core point of each POC" into the main app involves manually finding and copy-pasting hundreds of scattered lines of code. This is not flawless and is highly prone to regressions (e.g., overwriting a fix from PoC 01 when pasting PoC 02).

## 2. Plan to Organize PoCs (The Fix)

To make each PoC more organized and facilitate flawless integration:

### A. Extract PoC-Specific Code into Modules
Instead of modifying [main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py) and [index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/pocs/01_interruption/index.html) directly with inline code, we will extract the core logic of each PoC into its own dedicated class or module file within the PoC directory, or clearly demarcated blocks.
- **Backend:** Create isolated files like `pocs/01_interruption/interruption_handler.py`.
- **Frontend:** Extract JavaScript logic into `pocs/01_interruption/interruption_client.js` or well-defined JS classes (`class InterruptionManager { ... }`).

### B. Use Diffing to Isolate Changes
We will use diff tools to compare each PoC's [main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py)/[index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/pocs/01_interruption/index.html) against the main app's baseline. This will perfectly identify the "core points" to be extracted.

### C. Feature Flags / Plug-and-Play Architecture
In the main app, we will introduce a clean "hook" or "feature flag" system. 
For example, in [main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py), instead of hardcoding PoC 02's proactive vision timer, we will have:
```python
if POC_CONFIG["proactive_vision_enabled"]:
    proactive_vision_handler.check_idle_state(runtime_state)
```
This allows the main app to safely absorb all PoCs without them interfering with each other unless explicitly turned on.

## 3. Integration into the Core Product (The Final Step)

Once the logic is isolated and modularized:
1. **Move Modules:** Move the isolated PoC modules (e.g., `interruption_handler.py`, `proactive_vision.py`) into the `backend/` and `frontend/` directories.
2. **Wire Hooks:** Add the hooks to [backend/main.py](file:///Users/luisguimaraes/Projects/SeeMeTutor/backend/main.py) and [frontend/index.html](file:///Users/luisguimaraes/Projects/SeeMeTutor/frontend/index.html).
3. **Flow Management:** Ensure the PoCs trigger at the right time. For example:
   - Ensure the VAD from PoC 01 correctly stops the audio queue.
   - Ensure PoC 02's proactive vision only triggers during actual silence and when the student is not currently speaking (coordinating with PoC 01's VAD).
4. **Validation:** Run the specific `Test Plan` defined in each PoC's [prd.md](file:///Users/luisguimaraes/Projects/SeeMeTutor/pocs/01_interruption/prd.md) on the **main app** to prove the flawless integration.

## Next Steps
If you approve of this architectural approach (Extract to Modules -> Add Feature Hooks to Main -> Wire together), I will begin with **Step 2: Implement the changes to make each PoC more organized**, starting with PoC 01 and PoC 02.
