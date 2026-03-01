# Canvas Post-It Whiteboard — Implementation Plan

**Goal:** Transform the whiteboard from a scrollable list into a visual canvas with floating post-it notes that agents populate dynamically.

**Version:** 1.0 — Non-interactive (read-only display), v2 will add drag-and-drop

---

## Visual Concept

Instead of a vertical scrolling list, notes appear as colorful post-it notes pinned to an infinite canvas background. Notes have:

- Realistic post-it styling (slight rotation, subtle shadow, color coding)
- Auto-positioned to avoid overlap
- Visual connections between related notes (lines/arrows showing progression)
- Animated entrance (notes "fly in" from edges)

```
┌─────────────────────────────────────────────────────────────┐
│  SeeMe Tutor                           [status]             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌────────┐          ┌────────┐                           │
│   │ 📝     │          │ 📝     │     ╭────────╮            │
│   │Exercise│          │Formula │     │Progress│            │
│   │   1    │          │        │     │ Ladder │            │
│   └────┬───┘          └────────┘     ╰────┬───╯            │
│        │                                   │                │
│        │    ┌────────┐                     │                │
│        └───►│Pattern │◄────────────────────┘                │
│             │Heatmap │                                      │
│             └────────┘                                      │
│                                                             │
│                    ┌────────┐                               │
│                    │Derivation│                             │
│                    │   Card  │                              │
│                    └────────┘                               │
│                                                             │
│  [Corkboard / Grid Paper Background]                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Post-It Color System

Each note type gets a distinct post-it color for instant visual recognition:

| Note Type | Post-It Color | Hex | Use Case |
|-----------|---------------|-----|----------|
| `checklist_item` | Classic Yellow | `#FEFF9C` | Exercise tasks |
| `formula` | Blue | `#7AFcff` | Math/science formulas |
| `insight` | Green | `#7CFC9C` | Key learnings, tips |
| `vocabulary` | Purple | `#E8A0FF` | Language learning |
| `summary` | Orange | `#FFBC7A` | Topic summaries |
| `progress_ladder` | Gradient Blue→Green | `#7AFcff` → `#7CFC9C` | Step progression |
| `pattern_analysis` | Red/Pink | `#FF7A7A` | Error patterns, warnings |
| `derivation` | Teal | `#7AFFD4` | Formula derivations |

**Visual Effects:**

- Slight rotation (-3° to +3° random) for organic feel
- Subtle drop shadow (float effect)
- Slight curl at corners (optional CSS trick)
- Pin graphic at top center

---

## Canvas Layout System (v1 — Auto-Layout)

Since v1 is non-interactive, we use an auto-layout algorithm to position notes.

### Layout Strategy: Spiral + Clustering

```
New notes enter from the right edge and spiral inward:

                    ┌─────┐
              ┌─────┘ new │
        ┌─────┘     └─────┘
   ┌────┘  ┌────┐
   │ older │ 2  │
   └───────┘────┘
```

**Algorithm:**

1. **Zone-based positioning** — Canvas divided into zones:
   - **Left zone:** Exercise/checklist items (student's "to-do")
   - **Center zone:** Formulas, insights (key learning content)
   - **Right zone:** Progress tracking, patterns (analytics)
   - **Bottom zone:** Derivations, summaries (reference material)

2. **Cascade within zones** — New notes in a zone stack with slight offset:

   ```
   Note 1: position (x, y)
   Note 2: position (x + 15px, y + 15px) — cascades down-right
   Note 3: position (x + 30px, y + 30px)
   ```

3. **Connection lines** — Related notes get SVG lines/arrows:
   - Exercise → Formula (uses formula to solve)
   - Pattern Analysis → Targeted Practice (suggested next step)
   - Progress Ladder steps connected vertically

### Position Data Structure

Backend sends position hints (zone + stack order), frontend calculates exact coordinates:

```json
{
  "id": "note_123",
  "type": "formula",
  "content": {...},
  "layout": {
    "zone": "center",
    "stack_index": 2,
    "connections": ["note_456", "note_789"]
  }
}
```

**Zone Coordinates** (relative to canvas):

```javascript
const ZONES = {
  left:   { x: 20,  y: 100, width: 140, height: 300 },
  center: { x: 180, y: 80,  width: 200, height: 350 },
  right:  { x: 400, y: 100, width: 160, height: 300 },
  bottom: { x: 100, y: 400, width: 380, height: 150 }
};
```

---

## Post-It Card Design

### CSS Structure

```css
/* Canvas container */
.whiteboard-canvas {
  position: relative;
  width: 100%;
  height: 100%;
  background: 
    /* Corkboard texture */
    repeating-linear-gradient(
      45deg,
      #d4a574 0px,
      #c99660 2px,
      #d4a574 4px
    ),
    /* Or: Grid paper */
    linear-gradient(#e8e8e8 1px, transparent 1px),
    linear-gradient(90deg, #e8e8e8 1px, transparent 1px);
  background-size: 100% 100%, 20px 20px, 20px 20px;
  overflow: hidden;
}

/* Post-it base */
.postit-note {
  position: absolute;
  width: 140px;
  min-height: 100px;
  padding: 12px;
  border-radius: 2px;
  box-shadow: 
    2px 3px 8px rgba(0,0,0,0.15),
    0 1px 2px rgba(0,0,0,0.1);
  font-family: 'Comic Sans MS', 'Chalkboard SE', cursive;
  transition: all 0.3s ease;
  animation: postit-enter 0.5s ease-out;
}

/* Color variants */
.postit-note.yellow { background: #FEFF9C; }
.postit-note.blue { background: #7AFcff; }
.postit-note.green { background: #7CFC9C; }
.postit-note.purple { background: #E8A0FF; }
.postit-note.orange { background: #FFBC7A; }
.postit-note.red { background: #FF7A7A; }
.postit-note.teal { background: #7AFFD4; }

/* Pin at top */
.postit-note::before {
  content: '';
  position: absolute;
  top: -6px;
  left: 50%;
  transform: translateX(-50%);
  width: 12px;
  height: 12px;
  background: radial-gradient(circle at 30% 30%, #ff6b6b, #c92a2a);
  border-radius: 50%;
  box-shadow: 1px 1px 3px rgba(0,0,0,0.3);
}

/* Slight rotation for organic feel */
.postit-note:nth-child(odd) { transform: rotate(-1deg); }
.postit-note:nth-child(even) { transform: rotate(1deg); }
.postit-note:nth-child(3n) { transform: rotate(-2deg); }
.postit-note:nth-child(5n) { transform: rotate(2deg); }

/* Entrance animation */
@keyframes postit-enter {
  from {
    opacity: 0;
    transform: translateX(50px) rotate(10deg) scale(0.8);
  }
  to {
    opacity: 1;
    transform: translateX(0) rotate(var(--rotation)) scale(1);
  }
}

/* Hover effect (even in v1 non-interactive) */
.postit-note:hover {
  transform: rotate(0deg) scale(1.05);
  box-shadow: 4px 6px 16px rgba(0,0,0,0.2);
  z-index: 100;
}
```

### Card Content Layout

```css
.postit-header {
  font-size: 11px;
  font-weight: bold;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
  color: rgba(0,0,0,0.6);
}

.postit-title {
  font-size: 13px;
  font-weight: bold;
  margin-bottom: 8px;
  line-height: 1.3;
  color: rgba(0,0,0,0.85);
}

.postit-content {
  font-size: 12px;
  line-height: 1.4;
  color: rgba(0,0,0,0.75);
}

/* Type-specific icons */
.postit-icon::before {
  margin-right: 4px;
}
.postit-formula .postit-icon::before { content: '📐'; }
.postit-insight .postit-icon::before { content: '💡'; }
.postit-vocabulary .postit-icon::before { content: '📝'; }
.postit-pattern .postit-icon::before { content: '🔍'; }
.postit-progress .postit-icon::before { content: '🪜'; }
.postit-derivation .postit-icon::before { content: '📜'; }
```

---

## Connection Lines (Progression Visualization)

SVG overlay on canvas shows relationships between notes:

```css
.connection-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}

.connection-line {
  stroke: rgba(66, 133, 244, 0.4);
  stroke-width: 2;
  fill: none;
  stroke-dasharray: 5,5;
  animation: dash-flow 20s linear infinite;
}

@keyframes dash-flow {
  to { stroke-dashoffset: -100; }
}

.connection-arrow {
  fill: rgba(66, 133, 244, 0.6);
}
```

**Connection Types:**

1. **Solid line** — Sequential relationship (steps in progression)
2. **Dashed line** — Related content (formula used for exercise)
3. **Animated flow** — Active progression (current step being worked on)

---

## Special Post-It Types (Visual Treatment)

### Progress Ladder Post-It

A vertical post-it showing step progression:

```
┌──────────────┐
│ 📌 🪜 Steps   │
├──────────────┤
│ ✓ Step 1     │ ← Green, struck through
│ ✓ Step 2     │ ← Green, struck through  
│ → Step 3     │ ← Blue, pulsing
│ ○ Step 4     │ ← Gray
└──────────────┘
```

**Visual:**

- Taller post-it (vertical orientation)
- Step icons change based on status
- Current step has pulsing border animation
- Completed steps have strikethrough

### Pattern Analysis Post-It

Horizontal post-it with mini bar chart:

```
┌─────────────────┐
│ 📌 🔍 Pattern   │
├─────────────────┤
│ Sign errors     │
│ ████████ 80%    │ ← Red bar
│ Calculation     │
│ ██░░░░░░ 20%    │ ← Yellow bar
└─────────────────┘
```

**Visual:**

- Red/pink post-it color
- Bars animate filling on entrance
- Primary pattern highlighted with icon

### Derivation Post-It

Large post-it with expandable steps:

```
┌───────────────────┐
│ 📌 📜 Quadratic   │
├───────────────────┤
│ Step 1:           │
│ ax² + bx + c = 0  │ ← Monospace math
│                   │
│ Step 2:           │
│ ax² + bx = -c     │
│ ↓                 │ ← Arrow
│ [Reveal Next]     │ ← Button-like
└───────────────────┘
```

**Visual:**

- Teal post-it color
- Math in monospace font
- Step arrows animated
- "Reveal" button styled as torn paper edge

---

## Animation Sequence

When a new note arrives:

1. **Entry** (0-500ms):
   - Note flies in from right edge
   - Rotation settles from random angle to final position
   - Scale goes from 0.8 to 1.0
   - Shadow expands

2. **Settle** (500-800ms):
   - Slight bounce effect
   - Rotation微调 to final value
   - Pin "drops" into place

3. **Connection** (800-1200ms):
   - SVG line draws from related note
   - Arrow head appears
   - Dashed animation starts

4. **Content Reveal** (staggered):
   - Title fades in
   - Content fades in 100ms later
   - Interactive elements (buttons) appear last

```javascript
// Animation sequence
function animateNoteEntry(noteElement) {
  // Phase 1: Fly in
  noteElement.style.animation = 'postit-enter 0.5s ease-out';
  
  // Phase 2: Draw connections after settle
  setTimeout(() => {
    drawConnections(noteElement.id);
  }, 500);
  
  // Phase 3: Content stagger
  const content = noteElement.querySelector('.postit-content');
  content.style.opacity = '0';
  setTimeout(() => {
    content.style.transition = 'opacity 0.3s';
    content.style.opacity = '1';
  }, 300);
}
```

---

## Background Options

### Option A: Corkboard (Classic)

```css
background: 
  repeating-linear-gradient(
    45deg,
    #d4a574 0px,
    #c99660 2px,
    #d4a574 4px
  );
```

### Option B: Grid Paper (Academic)

```css
background: 
  linear-gradient(#e0e0e0 1px, transparent 1px),
  linear-gradient(90deg, #e0e0e0 1px, transparent 1px),
  #f5f5f5;
background-size: 20px 20px;
```

### Option C: Chalkboard (Dark Mode)

```css
background: 
  radial-gradient(ellipse at center, #2a3b4c 0%, #1a2b3c 100%);
/* Post-its pop against dark background */
```

**Recommendation:** Option A (Corkboard) for warm, approachable feel OR Option B (Grid Paper) for academic context.

---

## Implementation Plan (v1)

### Phase 1: Canvas Container (Day 1)

1. Replace current whiteboard scroll container with canvas div
2. Add corkboard/grid background
3. Keep existing note data structure

### Phase 2: Post-It CSS (Day 2)

1. Create `.postit-note` base styles
2. Add color variants for each note type
3. Add pin graphic (::before pseudo-element)
4. Add rotation and shadow effects

### Phase 3: Layout Engine (Day 3)

1. Implement zone-based positioning
2. Add cascade offset for stacked notes
3. Calculate positions on note arrival
4. Store positions in note data

### Phase 4: Connection Lines (Day 4)

1. Add SVG overlay layer
2. Implement line drawing between related notes
3. Add arrow markers
4. Animate line drawing on connection

### Phase 5: Animations (Day 5)

1. Add entrance animation (fly-in)
2. Add hover effects
3. Add content stagger reveal
4. Polish timing and easing

### Phase 6: Special Cards (Day 6-7)

1. Progress ladder vertical layout
2. Pattern analysis mini-chart
3. Derivation step display
4. Test with all note types

---

## Files to Modify

### Frontend Only (v1)

1. `frontend/index.html`:
   - Replace whiteboard container structure
   - Add post-it CSS styles
   - Add canvas layout JavaScript
   - Add SVG connection layer
   - Update note rendering logic

### Backend (Optional — v1.5)

1. `backend/modules/whiteboard.py`:
   - Add `layout_hints` to note data structure
   - Suggest zone based on note type

---

## Demo "Wow" Moment (Canvas Edition)

**0:00-0:10** — Notes Flying In

- Student starts session
- **UI:** First post-it (exercise) flies in from right, settles with bounce
- Tutor starts explaining
- **UI:** Second post-it (formula) flies in, connects to first with animated line
- **Visual:** Corkboard background, colorful post-its, pins at top

**0:10-0:20** — Progress Ladder Appears

- Tutor: "Let's break this down..."
- **UI:** Tall blue post-it flies in, shows 4 steps
- Student completes step 1
- **UI:** Step 1 turns green with checkmark, step 2 starts pulsing
- **Visual:** Clear progression within the post-it

**0:20-0:30** — Pattern Detected

- Student makes errors
- **UI:** Red post-it flies in with bar chart
- **UI:** Line connects pattern post-it to exercise post-it
- Tutor: "I see a pattern here..."
- **Visual:** Multiple post-its connected by lines, showing agent reasoning

**Judge Sees:**

- ✅ Visual innovation — corkboard with post-its (not a chat interface)
- ✅ Agent reasoning — connections show how notes relate
- ✅ Color coding — instant recognition of note types
- ✅ Animated polish — professional, engaging feel

---

## v2 Ideas (Interactive — Future)

- Drag-and-drop to reposition notes
- Pin/unpin notes (toggle fixed position)
- Zoom and pan canvas
- Create new notes by double-clicking
- Group notes into clusters
- Draw freehand connections

---

## Summary

The canvas post-it whiteboard transforms the UI from a utilitarian list into a visually rich, interactive board that:

1. **Differentiates from chatbots** — Physical, tangible metaphor (real corkboard)
2. **Shows agent intelligence** — Connection lines reveal relationships
3. **Engages visually** — Colors, animations, playful post-it metaphor
4. **Scales content** — Auto-layout prevents clutter
5. **Feels alive** — Notes fly in, connections animate, content reveals progressively

This is the kind of UI innovation that wins the 40% "Innovation & Multimodal UX" score.
