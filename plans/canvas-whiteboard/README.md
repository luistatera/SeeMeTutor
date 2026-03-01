# Canvas Whiteboard Redesign — Master Plan

**Goal:** Transform the SeeMe Tutor whiteboard from a scrolling list into a visual canvas with post-it notes, animated connections, and a complete learning journey visualization.

**Stack:** Muuri (layout) + LeaderLine (connections) + Custom CSS (post-it styling)

---

## Plan Documents

| Document | Purpose | Read This If... |
|----------|---------|-----------------|
| **[01-agentic-features.md](01-agentic-features.md)** | 3 high-impact agentic features | You want to understand what agents will create on the board |
| **[02-canvas-concept.md](02-canvas-concept.md)** | Visual concept & post-it design | You want to see the visual design system |
| **[03-component-evaluation.md](03-component-evaluation.md)** | Open source library evaluation | You want to understand why we chose Muuri + LeaderLine |
| **[04-visual-learning-journey.md](04-visual-learning-journey.md)** | Complete learning journey UX | You want the full user experience flow |
| **[05-implementation-guide.md](05-implementation-guide.md)** | Technical implementation steps | You're ready to build |

---

## Quick Summary

### The 3 Agentic Features

1. **Progress Ladder** — Step-by-step problem breakdown
2. **Pattern Heatmap** — Visual error pattern analysis  
3. **Formula Derivation** — Interactive step-by-step derivations

### Visual Design

- **6 post-it types:** Concept (yellow), Formula (blue), Example (green), Practice (white + border), Pattern (red), Mastery (gold)
- **4 connection styles:** Forward progress (solid blue), Learning loop (dashed red), Mastery path (green glow), Optional (dotted gray)
- **Animations:** Post-it fly-in, line draw, state transitions, pulse effects

### Technology Stack

```html
<!-- Muuri for layout -->
<script src="https://cdn.jsdelivr.net/npm/muuri@0.9.5/dist/muuri.min.js"></script>

<!-- LeaderLine for connections -->
<script src="https://cdn.jsdelivr.net/npm/leader-line@1.0.7/leader-line.min.js"></script>
```

### Learning Journey Flow

```
CONCEPT ──► FORMULA ──► EXAMPLE ──► PRACTICE ──┐
   🟡          🔵          🟢          📝        │
                    ┌───────────────────────────┼───┐
                    ▼                           ▼   ▼
              STRUGGLE ◄────Loop────► MASTERED
                 🔴                         🏆
```

---

## Implementation Timeline

| Day | Task | Document |
|-----|------|----------|
| 1 | Setup Muuri + LeaderLine | [05-implementation-guide.md](05-implementation-guide.md) |
| 2 | Post-it design system | [02-canvas-concept.md](02-canvas-concept.md) |
| 3 | Connection lines | [04-visual-learning-journey.md](04-visual-learning-journey.md) |
| 4 | Learning flow logic | [04-visual-learning-journey.md](04-visual-learning-journey.md) |
| 5 | Agent integration | [01-agentic-features.md](01-agentic-features.md) |
| 6 | Animations & polish | [02-canvas-concept.md](02-canvas-concept.md) |
| 7 | Demo flow & testing | All docs |

---

## Key Files to Modify

### Frontend

- `frontend/index.html` — Add libraries, post-it CSS, connection JS

### Backend (Optional)

- `backend/modules/whiteboard.py` — Add `connections` and `learning_state` fields
- `backend/agent.py` — Add new tools for agent-created content

---

## Success Criteria

- [ ] Learning journey clear in <5 seconds
- [ ] All animations run at 60fps
- [ ] Demo flow works 100% of time
- [ ] Visual shows concept → mastery progression
- [ ] Struggle loop clearly visualized

---

## Start Here

1. Read **[04-visual-learning-journey.md](04-visual-learning-journey.md)** for the big picture
2. Read **[05-implementation-guide.md](05-implementation-guide.md)** for technical steps
3. Reference other docs for details as needed

---

**Created:** March 2026  
**Purpose:** SeeMe Tutor Hackathon — Canvas Whiteboard Redesign
