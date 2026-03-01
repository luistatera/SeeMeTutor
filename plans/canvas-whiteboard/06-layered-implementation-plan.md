# Layered Implementation Plan — Backend First, Then Frontend

**Approach:** Implement in 3 distinct layers: **Contract → Backend → Frontend**

This allows parallel workstreams and clear handoff points.

---

## Layer 1: Contract Definition (Day 1)

Define the data structures and API contracts first. Both Backend and Frontend teams agree on these.

### 1.1 New Note Types

```python
# backend/modules/whiteboard.py
VALID_NOTE_TYPES = {
    "insight", "checklist_item", "formula", "summary", "vocabulary",
    "progress_ladder",      # NEW
    "pattern_analysis",     # NEW  
    "derivation",           # NEW
    "mastery_badge"         # NEW
}
```

### 1.2 Note Data Structure with Connections

```json
{
  "id": "note_abc123",
  "type": "progress_ladder",
  "title": "Solving: Linear Equations",
  "content": {...},
  
  "// NEW: Canvas layout metadata": "",
  "layout": {
    "zone": "center",
    "position": {"x": 200, "y": 150},
    "rotation": -2,
    "z_index": 5
  },
  
  "// NEW: Learning journey connections": "",
  "connections": {
    "from": "note_previous_id",
    "to": ["note_next_1", "note_next_2"],
    "style": "forward"  // forward, loop, mastery, optional
  },
  
  "// NEW: Learning state": "",
  "learning_state": {
    "stage": "practice",  // concept, learn, practice, struggle, master
    "status": "in_progress",  // not_started, in_progress, completed, struggling, mastered
    "order": 3  // Position in learning journey (1, 2, 3...)
  }
}
```

### 1.3 New Tool Contracts

**StepPlannerAgent Tools:**

```python
# Tool: create_progress_ladder
Request: {
  "title": "Solving: Linear Equations",
  "steps": [
    {"text": "Isolate variable", "hint": "Get x alone"},
    {"text": "Move constants", "hint": "Balance equation"}
  ],
  "problem_type": "math",
  "connections": {"from": "concept_note_id"}  # Links to previous note
}

Response: {
  "ladder_id": "ladder_abc123",
  "note_id": "note_xyz789",
  "result": "created"
}

# Tool: update_step_status
Request: {
  "ladder_id": "ladder_abc123",
  "step_index": 1,
  "status": "completed"  # in_progress, completed, struggling
}

Response: {
  "ladder_id": "ladder_abc123",
  "current_step": 2,
  "completed_steps": 1,
  "result": "updated"
}
```

**PatternAnalyzerAgent Tools:**

```python
# Tool: analyze_error_patterns
Request: {
  "exercise_id": "exercise_123",
  "error_type": "sign_error",
  "context": "-3x = 15, student answered x = -5"
}

Response: {
  "pattern_detected": true,
  "pattern_type": "sign_errors",
  "frequency": 0.67,  # 67% of exercises
  "recommendation": "Practice negative number rules"
}

# Tool: generate_pattern_card
Request: {
  "pattern_type": "sign_errors",
  "patterns": [
    {"type": "sign_errors", "count": 4, "percent": 80},
    {"type": "calculation", "count": 1, "percent": 20}
  ],
  "source_exercise_id": "exercise_123",
  "connections": {"from": "exercise_123", "style": "loop"}
}

Response: {
  "note_id": "note_pattern_456",
  "result": "created"
}
```

**FormulaExplainerAgent Tools:**

```python
# Tool: create_derivation
Request: {
  "formula_name": "Quadratic Formula",
  "domain": "math",
  "steps": [
    {"math": "ax² + bx + c = 0", "explanation": "Standard form"},
    {"math": "ax² + bx = -c", "explanation": "Subtract c"}
  ],
  "connections": {"from": "formula_note_id"}
}

Response: {
  "derivation_id": "deriv_qf_001",
  "note_id": "note_deriv_789",
  "result": "created"
}
```

### 1.4 WebSocket Message Types

```javascript
// Existing
{type: "whiteboard", data: {...}}

// NEW: Connection update
{type: "connection", data: {
  "from_id": "note_1",
  "to_id": "note_2", 
  "style": "forward",
  "animated": true
}}

// NEW: Learning state update
{type: "learning_state", data: {
  "note_id": "note_123",
  "stage": "practice",
  "status": "struggling",
  "timestamp": "..."
}}
```

### Deliverable (End of Day 1)

- [ ] Updated `whiteboard.py` with new note types
- [ ] Data structure documentation
- [ ] Tool API contracts documented
- [ ] Frontend team has reviewed and approved contracts

---

## Layer 2: Backend Implementation (Days 2-5)

Backend team implements the tools and note types. Frontend can start basic scaffolding.

### Day 2: Core Note Type Infrastructure

**Files to modify:**

- `backend/modules/whiteboard.py`
  - Add new note types to `VALID_NOTE_TYPES`
  - Add normalization functions for new types
  - Add layout metadata support
  - Add connection metadata support

**Testing:**

```python
# Test new note types
note = {
  "type": "progress_ladder",
  "title": "Test Ladder",
  "layout": {"zone": "center", "rotation": -2},
  "connections": {"from": None, "to": []}
}
normalized = normalize_note_type(note["type"])
assert normalized == "progress_ladder"
```

### Day 3: StepPlannerAgent

**Files to create:**

- `backend/agents/step_planner.py`

**Tools to implement:**

- `create_progress_ladder`
- `update_step_status`

**Key logic:**

- Generate unique ladder_id
- Store step states in Firestore
- Call `write_notes` with `note_type="progress_ladder"`
- Update connections when steps complete

**Integration:**

- Add to Coordinator routing
- Trigger when multi-step problem detected

### Day 4: PatternAnalyzerAgent

**Files to create:**

- `backend/agents/pattern_analyzer.py`

**Tools to implement:**

- `log_error` (track errors in session state)
- `analyze_error_patterns`
- `generate_pattern_card`

**Key logic:**

- Track errors per session in `runtime_state["error_patterns"]`
- Threshold: 3+ errors of same type = pattern detected
- Generate pattern card with bar chart data
- Create loop connection back to new practice

### Day 5: FormulaExplainerAgent + Integration

**Files to create:**

- `backend/agents/formula_explainer.py`
- `backend/data/derivations_library.json` (pre-built derivations)

**Tools to implement:**

- `create_derivation`
- `reveal_derivation_step`

**Integration:**

- Wire all agents into Coordinator
- Test end-to-end flow
- Verify WebSocket messages sent correctly

### Backend Deliverables (End of Day 5)

- [ ] All 3 agents implemented and tested
- [ ] New note types working with `write_notes`
- [ ] Connection metadata flowing through WebSocket
- [ ] Firestore storing learning state
- [ ] Unit tests for all tools

---

## Layer 3: Frontend Implementation (Days 6-11)

Frontend team builds the visualization layer using the contracts defined in Layer 1.

### Day 6: Setup & Infrastructure

**Files to modify:**

- `frontend/index.html`

**Tasks:**

- Add Muuri CDN: `https://cdn.jsdelivr.net/npm/muuri@0.9.5/dist/muuri.min.js`
- Add LeaderLine CDN: `https://cdn.jsdelivr.net/npm/leader-line@1.0.7/leader-line.min.js`
- Create canvas container structure
- Initialize Muuri grid
- Test with static post-its

```javascript
// Initialize Muuri
const grid = new Muuri('.whiteboard-canvas', {
  items: '.postit-note',
  layout: { fillGaps: true },
  dragEnabled: false  // v1
});
```

### Day 7: Post-It Design System

**CSS to add:**

- Base `.postit-note` styles
- Color variants (yellow, blue, green, red, teal, gold)
- Pin graphic (::before pseudo-element)
- Rotation and shadow effects
- Entrance animations

**JavaScript:**

- `createPostItElement(noteData)` function
- Color mapping by note type
- Rotation randomization (-3° to +3°)

### Day 8: Connection Lines (LeaderLine)

**JavaScript to add:**

- Draw connections when notes arrive
- 4 line styles (forward, loop, mastery, optional)
- Line animation on connection
- Arrow markers

```javascript
function drawConnection(fromEl, toEl, style) {
  const options = getLineOptions(style);
  return new LeaderLine(fromEl, toEl, options);
}

function getLineOptions(style) {
  switch(style) {
    case 'forward':
      return { color: '#4285F4', size: 3 };
    case 'loop':
      return { 
        color: '#EA4335', 
        size: 2, 
        dash: { animation: true },
        path: 'arc'
      };
    // ... etc
  }
}
```

### Day 9: Learning Journey Visualization

**JavaScript to add:**

- Track learning state from WebSocket
- Update post-it borders by status (not_started, in_progress, completed, struggling, mastered)
- Show/hide connections based on state
- Animate state transitions

**Visual states:**

```css
.postit-note[data-status="not_started"] { border: 2px solid #FBBC04; }
.postit-note[data-status="in_progress"] { border: 2px solid #4285F4; }
.postit-note[data-status="completed"] { border: 2px solid #34A853; }
.postit-note[data-status="struggling"] { border: 2px solid #EA4335; }
.postit-note[data-status="mastered"] { 
  border: 3px solid #FFD700;
  box-shadow: 0 0 20px rgba(255, 215, 0, 0.5);
}
```

### Day 10: Special Card Types

**Progress Ladder Card:**

- Vertical layout with steps
- Status icons (○ → → ✓)
- Pulsing current step
- Progress bar

**Pattern Analysis Card:**

- Horizontal bar chart
- Animated bar fills
- Color coding by frequency

**Derivation Card:**

- Step-by-step math display
- Monospace font for equations
- Reveal animations

### Day 11: Animations & Polish

**Animations to implement:**

- Post-it entrance (fly in from right, settle with bounce)
- Line draw animation
- State transition effects (border flash, pulse)
- Confetti on mastery
- Connection line dash animation

**Performance:**

- Use CSS transforms (GPU accelerated)
- Throttle Muuri layout updates
- Lazy-load LeaderLine connections

### Frontend Deliverables (End of Day 11)

- [ ] All 6 post-it types rendering correctly
- [ ] 4 connection line styles working
- [ ] Learning journey flow visualized
- [ ] State transitions animated
- [ ] Demo flow tested end-to-end

---

## Integration Testing (Day 12)

Test the full stack:

### Test Scenarios

**Scenario 1: Quick Mastery**

```
1. Agent creates concept note → Frontend renders yellow post-it
2. Agent creates formula note + connection → Blue line draws
3. Agent creates practice note + connection → White post-it, blue border
4. Student succeeds → Border turns green, mastery line draws, gold badge appears
```

**Scenario 2: Learning Loop**

```
1. Practice note created → White post-it, blue border
2. Student makes errors → Border turns red
3. PatternAnalyzer creates pattern card → Red post-it appears below
4. Red loop connection draws (animated dashes)
5. New practice created → Loop continues
6. Student succeeds → Green line to mastery
```

### Integration Points to Verify

- [ ] WebSocket messages flow correctly
- [ ] Note creation triggers Muuri add
- [ ] Connection data triggers LeaderLine
- [ ] State updates animate correctly
- [ ] Agent state ticker syncs with visuals

---

## Parallel Workstreams

### Backend Team (Days 2-5)

- Day 2: Note type infrastructure
- Day 3: StepPlannerAgent
- Day 4: PatternAnalyzerAgent
- Day 5: FormulaExplainerAgent + integration

### Frontend Team (Days 6-11)

- Days 2-5: Can scaffold using mock data based on Layer 1 contracts
- Day 6: Setup Muuri + LeaderLine
- Day 7: Post-it design
- Day 8: Connections
- Day 9: Learning journey
- Day 10: Special cards
- Day 11: Polish

### Handoff Points

- **End of Day 1:** Contract finalized, both teams have specs
- **End of Day 5:** Backend sends real WebSocket messages, frontend switches from mock to real
- **Day 12:** Full integration testing

---

## Benefits of Layered Approach

1. **Clear contracts** — No confusion about data structures
2. **Parallel work** — Frontend can scaffold while backend builds
3. **Easier testing** — Test each layer independently
4. **Faster iteration** — Change contracts early, not mid-implementation
5. **Better collaboration** — Two developers can work simultaneously

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Contract changes mid-stream | Spend extra time on Day 1, get both teams to sign off |
| Backend delays frontend | Frontend uses mock data based on contracts until Day 5 |
| Integration issues | Daily sync meetings, test with real data on Day 6 |
| Performance problems | Test animations on target device early (Day 7) |

---

## Summary

**Layer 1 (Day 1):** Contracts — Define everything  
**Layer 2 (Days 2-5):** Backend — Build the engine  
**Layer 3 (Days 6-11):** Frontend — Build the visualization  
**Integration (Day 12):** Make it work together

**Total: 12 days** (was 10, but added contract day and integration day)

This approach is more robust and allows parallel development, which is critical for hackathon timelines.
