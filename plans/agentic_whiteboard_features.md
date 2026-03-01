# Agentic Whiteboard Features — Implementation Plan

**Goal:** Leverage backend agents to create dynamic, interactive whiteboard content that impresses judges and students by exposing the reasoning trace and providing visual learning aids.

---

## Executive Summary

Based on the PRD's winning thesis, we need features that prove "This is not a chatbot with a camera attached." These three features leverage the existing multi-agent architecture to create visual, interactive content on the whiteboard that demonstrates:

1. **Deterministic agent execution** (reasoning trace visible)
2. **Proactive intelligence** (agents create content without explicit requests)
3. **Pedagogical depth** (visual learning aids, not just text)

---

## Feature 1: Progress Ladder / Step Cards

### Concept

When the tutor guides a student through a multi-step problem, a `StepPlannerAgent` automatically breaks the solution into a visual progression ladder. Steps animate from pending → in_progress → done as the student progresses.

### Judge "Wow" Moment

Student works through a math problem. The whiteboard shows:

```
┌─────────────────────────────────┐
│ 🪜 Solving: Linear Equations    │
├─────────────────────────────────┤
│ ✓ Step 1: Isolate variable      │  [green, strikethrough]
│ ✓ Step 2: Move constants        │  [green, strikethrough]
│ → Step 3: Divide both sides     │  [blue pulse, animated]
│ ○ Step 4: Check answer          │  [gray, dimmed]
└─────────────────────────────────┘
     ↑ progress bar fills as steps complete
```

Agent State Ticker shows: `[StepPlannerAgent: Analyzing problem → Generating 4-step breakdown → Monitoring student progress]`

### Backend Implementation

**New Agent:** `StepPlannerAgent` (sub-agent under Coordinator)

**New Tool:** `create_progress_ladder`

```python
async def create_progress_ladder(
    title: str,
    steps: list[dict],  # [{"text": "...", "hint": "..."}]
    problem_type: str,  # "math", "grammar", "chemistry", etc.
    tool_context: ToolContext
) -> dict
```

**Tool Behavior:**

1. Generates unique ladder_id
2. Calls `write_notes` with new `note_type="progress_ladder"`
3. Stores step state in Firestore for persistence
4. Returns ladder_id for subsequent updates

**New Tool:** `update_step_status`

```python
async def update_step_status(
    ladder_id: str,
    step_index: int,
    status: str,  # "in_progress", "done", "struggling"
    tool_context: ToolContext
) -> dict
```

**Trigger Conditions:**

- Tutor enters `tutoring` phase with a multi-step problem
- Student successfully completes a step (detected via conversation analysis)
- Student struggles on a step (detected via frustration signals or incorrect answers)

**Agent State Trace Messages:**

- `"StepPlannerAgent: Analyzing problem structure..."`
- `"StepPlannerAgent: Generating 4-step breakdown..."`
- `"StepPlannerAgent: Step 2 completed → Advancing to Step 3..."`
- `"StepPlannerAgent: Detecting struggle → Inserting hint..."`

### Frontend Implementation

**New Note Type:** `progress_ladder`

**Data Structure:**

```json
{
  "id": "ladder_abc123",
  "type": "progress_ladder",
  "title": "Solving: Linear Equations",
  "steps": [
    {"text": "Isolate the variable", "hint": "Get x alone on one side", "status": "done"},
    {"text": "Move constants", "hint": "Add/subtract to balance", "status": "done"},
    {"text": "Divide both sides", "hint": "Divide by the coefficient", "status": "in_progress"},
    {"text": "Check your answer", "hint": "Plug back into original", "status": "pending"}
  ],
  "current_step": 2,
  "completed_count": 2,
  "total_count": 4
}
```

**UI Components:**

1. **Ladder Container** — Card with vertical flex layout
2. **Step Items** — Each step has:
   - Status icon (○ → → ✓ or ⚠)
   - Step text
   - Expandable hint (revealed on hover/tap)
   - Animated border color transition
3. **Progress Bar** — Bottom bar showing % complete
4. **Completion Badge** — Checkmark animation when all steps done

**CSS Animations:**

```css
/* Step status transitions */
.step-item { transition: all 0.3s ease; }
.step-item.in_progress { border-left: 3px solid var(--blue); animation: pulse-blue 2s infinite; }
.step-item.done { border-left: 3px solid var(--green); opacity: 0.7; }
.step-item.struggling { border-left: 3px solid var(--red); }

/* Progress bar fill */
@keyframes progress-fill {
  from { width: 0%; }
  to { width: var(--progress-percent); }
}
```

**JavaScript Logic:**

- On new ladder: animate card entrance from bottom
- On step update: animate status icon morph (○ → → ✓)
- Auto-scroll to keep current step visible
- Confetti animation on ladder completion

---

## Feature 2: Mistake Pattern Heatmap

### Concept

A `PatternAnalyzerAgent` tracks student errors across the session and generates a visual "error pattern card" when it detects recurring mistakes. This demonstrates proactive intelligence and personalized learning.

### Judge "Wow" Moment

After a few exercises, the tutor says: "I'm noticing a pattern in your work..." and the whiteboard reveals:

```
┌──────────────────────────────────────┐
│ 🔍 Pattern Detected: Sign Errors     │
├──────────────────────────────────────┤
│ Visual heatmap:                      │
│   Sign errors    ████████░░  80%     │
│   Calculation    ██░░░░░░░░  20%     │
│   Concept        ░░░░░░░░░░   0%     │
│                                      │
│ "You often miss negative signs.      │
│  Let's practice with these!"         │
│                                      │
│ [Generate Targeted Practice]         │
└──────────────────────────────────────┘
```

Agent State Ticker: `[PatternAnalyzer: Tracking errors → Recurring pattern detected → Generating visualization]`

### Backend Implementation

**New Agent:** `PatternAnalyzerAgent` (runs periodically + on demand)

**New Tool:** `analyze_error_patterns`

```python
async def analyze_error_patterns(
    min_observations: int = 3,  # Minimum errors before pattern detected
    tool_context: ToolContext
) -> dict
```

**Error Tracking State:**

```python
# Stored in runtime_state["error_patterns"]
{
    "sign_errors": {"count": 4, "examples": [...]},
    "calculation": {"count": 1, "examples": [...]},
    "conceptual": {"count": 0, "examples": []},
    "total_exercises": 5
}
```

**Pattern Detection Logic:**

1. Tutor calls `log_error(error_type, context)` during conversation
2. After each error, `PatternAnalyzerAgent` runs analysis
3. If any error_type exceeds threshold (e.g., 3 occurrences), generate heatmap
4. Heatmap shows relative frequency of each error type

**New Tool:** `generate_pattern_card`

```python
async def generate_pattern_card(
    pattern_type: str,  # "sign_errors", "calculation", etc.
    frequency_percent: int,
    suggestion: str,
    tool_context: ToolContext
) -> dict
```

**Tool Behavior:**

- Calls `write_notes` with `note_type="pattern_analysis"`
- Includes frequency data for visualization
- Includes personalized suggestion text

**Trigger Conditions:**

- 3+ errors of the same type detected
- Student explicitly asks "What am I doing wrong?"
- End of session summary generation

**Agent State Trace Messages:**

- `"PatternAnalyzer: Tracking error types..."`
- `"PatternAnalyzer: Sign error pattern detected (4/5 exercises)..."`
- `"PatternAnalyzer: Generating targeted practice suggestions..."`

### Frontend Implementation

**New Note Type:** `pattern_analysis`

**Data Structure:**

```json
{
  "id": "pattern_xyz789",
  "type": "pattern_analysis",
  "primary_pattern": "sign_errors",
  "patterns": [
    {"type": "sign_errors", "label": "Sign errors", "count": 4, "percent": 80},
    {"type": "calculation", "label": "Calculation", "count": 1, "percent": 20},
    {"type": "conceptual", "label": "Conceptual", "count": 0, "percent": 0}
  ],
  "suggestion": "You often miss negative signs. Let's practice with these!",
  "action_button": {
    "label": "Generate Targeted Practice",
    "action": "generate_practice",
    "params": {"focus": "sign_errors"}
  }
}
```

**UI Components:**

1. **Pattern Header** — Icon + pattern name
2. **Horizontal Bar Chart** — Visual frequency representation
   - Each bar animated to fill to its percentage
   - Color-coded: red for primary pattern, yellow for secondary, gray for none
3. **Insight Text** — Personalized observation
4. **Action Button** — Generates targeted practice problems

**CSS Animations:**

```css
/* Bar chart fill animation */
.pattern-bar {
  height: 12px;
  border-radius: 6px;
  background: var(--bg-card-soft);
  overflow: hidden;
}
.pattern-bar-fill {
  height: 100%;
  border-radius: 6px;
  width: 0%;
  animation: bar-fill 0.8s ease-out forwards;
}
@keyframes bar-fill {
  to { width: var(--fill-percent); }
}

/* Primary pattern highlight */
.pattern-item.primary {
  background: rgba(234, 67, 53, 0.1);
  border-left: 3px solid var(--red);
}
```

**JavaScript Logic:**

- Animate bars filling on card entrance
- Button click sends message to backend to generate practice
- Show tooltip with example errors on bar hover

---

## Feature 3: Interactive Formula Derivation

### Concept

Instead of just displaying formulas, the `FormulaExplainerAgent` creates step-by-step derivations that "build" on the whiteboard as the tutor explains. Each step can be expanded to see the reasoning.

### Judge "Wow" Moment

Tutor explains quadratic formula derivation. Whiteboard shows:

```
┌─────────────────────────────────────────┐
│ 📐 Deriving: Quadratic Formula          │
├─────────────────────────────────────────┤
│ Step 1: Start with standard form        │
│         ax² + bx + c = 0                │
│              ↓                          │
│ Step 2: Subtract c from both sides      │
│         ax² + bx = -c                   │
│              ↓ [animating arrow]        │
│ Step 3: Divide by a...                  │
│         [Reveal Next →]                 │
└─────────────────────────────────────────┘
```

Student can tap "Reveal Next" or tutor reveals automatically as they speak. Each step appears with a subtle animation.

Agent State Ticker: `[FormulaExplainerAgent: Loading derivation steps → Step 1/6 → Step 2/6...]`

### Backend Implementation

**New Agent:** `FormulaExplainerAgent` (specialized for math/science formulas)

**New Tool:** `create_derivation`

```python
async def create_derivation(
    formula_name: str,
    domain: str,  # "math", "physics", "chemistry"
    steps: list[dict],  # [{"math": "...", "explanation": "..."}]
    auto_reveal: bool = False,  # If true, tutor controls reveal timing
    tool_context: ToolContext
) -> dict
```

**Tool Behavior:**

1. Validates formula structure
2. Generates derivation_id
3. Calls `write_notes` with `note_type="derivation"`
4. Stores full derivation in Firestore for reference

**New Tool:** `reveal_derivation_step`

```python
async def reveal_derivation_step(
    derivation_id: str,
    step_index: int,
    tool_context: ToolContext
) -> dict
```

**Pre-built Derivations Library:**
Store common derivations in Firestore for quick retrieval:

- Quadratic formula
- Pythagorean theorem
- Derivative rules (power rule, chain rule)
- Physics formulas (F=ma derivation, etc.)

**Trigger Conditions:**

- Tutor says "Let me show you how this formula is derived..."
- Student asks "Where does this formula come from?"
- Tutor wants to explain concept from first principles

**Agent State Trace Messages:**

- `"FormulaExplainerAgent: Loading quadratic formula derivation..."`
- `"FormulaExplainerAgent: Preparing 6-step breakdown..."`
- `"FormulaExplainerAgent: Revealing Step 3/6..."`

### Frontend Implementation

**New Note Type:** `derivation`

**Data Structure:**

```json
{
  "id": "deriv_qf_001",
  "type": "derivation",
  "formula_name": "Quadratic Formula",
  "domain": "math",
  "steps": [
    {
      "step_num": 1,
      "math": "ax² + bx + c = 0",
      "explanation": "Start with standard quadratic form",
      "revealed": true
    },
    {
      "step_num": 2,
      "math": "ax² + bx = -c",
      "explanation": "Subtract c from both sides",
      "revealed": true
    },
    {
      "step_num": 3,
      "math": "x² + (b/a)x = -c/a",
      "explanation": "Divide all terms by a",
      "revealed": false
    }
  ],
  "current_step": 2,
  "total_steps": 6,
  "auto_reveal": false
}
```

**UI Components:**

1. **Derivation Header** — Formula name + domain icon
2. **Step List** — Vertical stack of steps
   - Each step: math expression (monospace), explanation text
   - Animated arrow (↓) between steps
   - Hidden steps show "[Tap to reveal]" or blur effect
3. **Navigation Controls** — "Previous" / "Next" buttons (if manual reveal)
4. **Step Counter** — "Step 2 of 6" indicator

**CSS Animations:**

```css
/* Step reveal animation */
.derivation-step {
  opacity: 0;
  transform: translateY(-10px);
  animation: step-reveal 0.4s ease-out forwards;
}
@keyframes step-reveal {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Arrow pulse between steps */
.step-arrow {
  animation: arrow-pulse 1.5s infinite;
}
@keyframes arrow-pulse {
  0%, 100% { opacity: 0.4; transform: translateY(0); }
  50% { opacity: 1; transform: translateY(3px); }
}

/* Math expression styling */
.step-math {
  font-family: 'Courier New', monospace;
  background: var(--bg-card-soft);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 15px;
}
```

**JavaScript Logic:**

- New steps animate in sequentially with 100ms stagger
- Hidden steps show gradient blur overlay
- Tap/click reveals next step with animation
- Auto-scroll to keep latest step visible

---

## Multi-Agent Architecture Integration

```
┌─────────────────────────────────────────────────────────────┐
│                    Coordinator Agent                        │
│              (Routes to appropriate sub-agent)              │
└─────────────┬───────────────────┬───────────────────────────┘
              │                   │
    ┌─────────▼─────────┐  ┌──────▼──────────┐
    │ StepPlannerAgent  │  │FormulaExplainer │
    │                   │  │     Agent       │
    │ create_progress_  │  │                 │
    │    ladder()       │  │ create_         │
    │ update_step_      │  │  derivation()   │
    │    status()       │  │ reveal_step()   │
    └─────────┬─────────┘  └──────┬──────────┘
              │                   │
              │   ┌───────────────▼──────────┐
              │   │  PatternAnalyzerAgent    │
              │   │                          │
              └──►│  analyze_error_patterns()│
                  │  generate_pattern_card() │
                  └────────────┬─────────────┘
                               │
                    ┌──────────▼──────────┐
                    │   write_notes()     │
                    │   (existing tool)   │
                    └─────────────────────┘
```

### Agent Handoff Protocol

**Coordinator → StepPlannerAgent:**

- Trigger: Problem with multiple steps detected
- Context: Problem statement, subject domain, student level
- Expected: Progress ladder created, step updates as student progresses

**Coordinator → FormulaExplainerAgent:**

- Trigger: Formula explanation requested
- Context: Formula name, derivation complexity preference
- Expected: Derivation card created, steps revealed in sync with speech

**Coordinator → PatternAnalyzerAgent:**

- Trigger: Multiple errors detected OR end of exercise
- Context: Session error history, student struggle count
- Expected: Pattern analysis card if threshold met

---

## Demo Script (30-Second "Wow" Moment)

**Setup:** Student profile with pre-loaded math exercises

**0:00-0:10** — Progress Ladder Moment

- Student: "Can you help me solve 3x + 7 = 22?"
- Tutor: "Absolutely! Let me break this down into steps..."
- **UI:** Agent State Ticker shows `[StepPlannerAgent: Analyzing problem → Generating step breakdown]`
- **UI:** Progress Ladder card animates in with 4 steps
- Student solves Step 1 correctly
- **UI:** Step 1 animates to "done" (green checkmark), Step 2 pulses blue

**0:10-0:20** — Pattern Detection Moment

- Student makes sign error on next problem: "-3x = 15" → says "x = -5" (wrong)
- Tutor gently corrects
- Student makes another sign error on third problem
- **UI:** Agent State Ticker shows `[PatternAnalyzer: Tracking errors → Recurring pattern detected]`
- **UI:** Pattern Analysis card appears showing "Sign errors: 67%" with visual bar
- Tutor: "I notice a pattern — you're missing negative signs. Let's focus on that!"

**0:20-0:30** — Formula Derivation Moment

- Student: "Where does the quadratic formula come from?"
- Tutor: "Great question! Let me show you how it's derived..."
- **UI:** Agent State Ticker shows `[FormulaExplainerAgent: Loading derivation → Step 1/6]`
- **UI:** Derivation card appears with first 2 steps visible
- Tutor explains Step 1 → Step 2 reveals automatically with animation
- Student taps "Reveal Next" to see Step 3

**Judge Sees:**

- ✅ Agents working autonomously (reasoning trace visible)
- ✅ Visual learning aids appearing dynamically
- ✅ Proactive pattern detection
- ✅ Interactive, engaging content (not static text)

---

## Implementation Phases

### Phase 1: Core Infrastructure (Day 1-2)

1. Add new note types to `whiteboard.py`:
   - `progress_ladder`
   - `pattern_analysis`
   - `derivation`
2. Update `VALID_NOTE_TYPES` constant
3. Add normalization functions for new types
4. Update frontend CSS for new card types

### Phase 2: StepPlannerAgent (Day 3-4)

1. Create `agents/step_planner.py`
2. Implement `create_progress_ladder` tool
3. Implement `update_step_status` tool
4. Add agent to Coordinator routing
5. Test with math problems

### Phase 3: PatternAnalyzerAgent (Day 5-6)

1. Create `agents/pattern_analyzer.py`
2. Implement error tracking in session state
3. Implement `analyze_error_patterns` tool
4. Implement `generate_pattern_card` tool
5. Add trigger logic in tutoring phase

### Phase 4: FormulaExplainerAgent (Day 7-8)

1. Create `agents/formula_explainer.py`
2. Create Firestore collection for derivation library
3. Implement `create_derivation` tool
4. Implement `reveal_derivation_step` tool
5. Pre-load 5-10 common derivations

### Phase 5: Frontend Polish (Day 9-10)

1. Implement progress ladder UI component
2. Implement pattern analysis bar chart
3. Implement derivation step reveal animations
4. Add confetti/completion animations
5. Test full demo flow

---

## Files to Modify

### Backend

1. `backend/modules/whiteboard.py` — Add new note types
2. `backend/agent.py` — Add new tools, agent routing
3. `backend/agents/step_planner.py` — New agent (create)
4. `backend/agents/pattern_analyzer.py` — New agent (create)
5. `backend/agents/formula_explainer.py` — New agent (create)
6. `backend/main.py` — Wire up new agents

### Frontend

1. `frontend/index.html` — Add CSS for new card types, JS rendering logic

### Infrastructure

1. Firestore: Create `derivations` collection
2. Firestore: Create `error_patterns` collection (for tracking)

---

## Success Metrics

- **Demo Reliability:** All 3 features work in 100% of demo runs
- **Latency:** Cards appear within 1 second of trigger
- **Animation Smoothness:** 60fps animations on target devices
- **Judge Comprehension:** Reasoning trace clearly shows agent activity

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Agent fails to detect step boundaries | Fallback: Tutor can manually trigger step updates via voice command "mark step complete" |
| Pattern detection false positives | Threshold tuning: Require 3+ errors before showing pattern card |
| Derivation steps too complex | Configurable complexity: "simple" vs "detailed" derivation modes |
| Frontend animation lag | Use CSS transforms only (GPU-accelerated), throttle re-renders |

---

## Conclusion

These three features transform the whiteboard from a passive note-taking surface into an active, intelligent learning companion. They demonstrate:

1. **Agentic depth** — Multiple specialized agents working together
2. **Visual innovation** — Charts, animations, interactive elements
3. **Pedagogical sophistication** — Pattern detection, scaffolded learning
4. **Reasoning transparency** — Judges see exactly what agents are doing

Combined with the existing proactive vision and affective dialogue features, these agentic whiteboard features create a compelling demonstration of truly intelligent tutoring.
