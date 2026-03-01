# Open Source Board/Canvas Components — Evaluation & Recommendation

**Goal:** Find the best open source component for the post-it canvas whiteboard instead of building from scratch.

**Constraint:** Frontend is vanilla HTML/JS (single-file PWA), no React build step preferred.

---

## Evaluation Criteria

| Criteria | Weight | Description |
|----------|--------|-------------|
| No-build integration | High | Must work in vanilla HTML/JS or via CDN |
| Visual appeal | High | Good-looking out of the box, customizable |
| Note/card display | High | Supports colorful cards/post-its |
| Canvas/pan-zoom | Medium | Ability to show many notes |
| Animation support | Medium | Entry animations, transitions |
| Bundle size | Medium | <200KB preferred for fast load |
| Maintenance | Medium | Active project, recent commits |

---

## Option 1: Muuri (Grid Layout Engine) — RECOMMENDED

**GitHub:** <https://github.com/haltu/muuri>  
**Demo:** <https://muuri.dev/>

### Overview

Muuri is a **JavaScript layout engine** that creates responsive, sortable, filterable grid layouts. It's the engine behind many kanban boards.

### Pros

- ✅ **No dependencies** — Pure vanilla JS
- ✅ **CDN available** — `<script src="https://cdn.jsdelivr.net/npm/muuri@0.9.5/dist/muuri.min.js">`
- ✅ **Lightweight** — ~25KB gzipped
- ✅ **Highly customizable** — Full control over item rendering
- ✅ **Animations** — Built-in FLIP animations (layout changes animate smoothly)
- ✅ **Responsive** — Auto-relayouts on resize
- ✅ **Active** — Recent updates, 11k+ GitHub stars

### Cons

- ❌ Not a "board" out of the box — you build the visual layer
- ❌ No built-in pan/zoom (would need to add separately)

### How It Works for SeeMe Tutor

```javascript
// 1. Create grid container
const grid = new Muuri('.whiteboard-canvas', {
  items: '.postit-note',
  layout: {
    fillGaps: true,
    horizontal: false,
    alignRight: false,
    alignBottom: false,
    rounding: false
  },
  dragEnabled: false, // v1: non-interactive
  showDuration: 400,
  showEasing: 'cubic-bezier(0.215, 0.61, 0.355, 1)',
  hideDuration: 400,
  hideEasing: 'cubic-bezier(0.215, 0.61, 0.355, 1)',
  visibleStyles: {
    opacity: 1,
    transform: 'scale(1)'
  },
  hiddenStyles: {
    opacity: 0,
    transform: 'scale(0.5)'
  }
});

// 2. Add new post-it
function addPostIt(noteData) {
  const el = createPostItElement(noteData);
  grid.add(el);
  // Muuri automatically animates it in
}
```

### Visual Customization

Muuri handles layout/animation, you handle the post-it styling:

```html
<div class="whiteboard-canvas">
  <div class="postit-note yellow">
    <div class="pin"></div>
    <div class="content">Exercise 1</div>
  </div>
</div>
```

**Verdict:** ⭐ **BEST CHOICE** — Lightweight, powerful, gives us full control over post-it styling while handling the hard layout/animation work.

---

## Option 2: React-Kanban (react-kanban)

**GitHub:** <https://github.com/asseinfo/react-kanban>  
**NPM:** `@asseinfo/react-kanban`

### Overview

A React-based kanban board component.

### Pros

- ✅ Beautiful out of the box
- ✅ Built-in drag-and-drop
- ✅ Column-based layout

### Cons

- ❌ **Requires React** — Our frontend is vanilla HTML/JS
- ❌ **Build step required** — Would need to add React build pipeline
- ❌ **Heavy** — React + dependencies = ~100KB+

**Verdict:** ❌ Not suitable — would require major frontend refactor.

---

## Option 3: React-Trello

**GitHub:** <https://github.com/rcdexta/react-trello>

### Overview

Trello-like board component for React.

### Pros

- ✅ Trello-style interface (familiar)
- ✅ Cards, lanes, drag-and-drop

### Cons

- ❌ **React only** — Same issue as react-kanban
- ❌ **Complex API** — Heavy component

**Verdict:** ❌ Not suitable — React dependency.

---

## Option 4: Excalidraw (Canvas Drawing)

**GitHub:** <https://github.com/excalidraw/excalidraw>  
**Demo:** <https://excalidraw.com/>

### Overview

Virtual whiteboard for sketching hand-drawn like diagrams.

### Pros

- ✅ **Beautiful hand-drawn aesthetic**
- ✅ **Pan/zoom canvas**
- ✅ **Supports text boxes/shapes**
- ✅ **Can be embedded** — npm package available
- ✅ CDN: `https://unpkg.com/@excalidraw/excalidraw@0.17.6/dist/excalidraw.production.min.js`

### Cons

- ❌ **Heavy** — ~500KB+ bundle
- ❌ **Overkill** — Full drawing tool, we just need notes
- ❌ **Complex integration** — API designed for full drawing app
- ❌ **Not designed for programmatic note placement** — Optimized for user drawing

### Possible Usage

Excalidraw has an `initialData` prop to pre-populate elements:

```javascript
const initialData = {
  elements: [
    {
      type: "rectangle",
      x: 100,
      y: 100,
      width: 140,
      height: 100,
      backgroundColor: "#FEFF9C",
      // ... other props
    }
  ]
};
```

But adding/updating elements programmatically is complex — Excalidraw expects to own the state.

**Verdict:** ❌ Overkill — too heavy, not designed for our use case.

---

## Option 5: Canvas API + Custom (No Library)

**Approach:** Use HTML5 Canvas API directly.

### Pros

- ✅ **Zero dependencies**
- ✅ **Full control**
- ✅ **Fast rendering**

### Cons

- ❌ **Complex** — Must handle all layout, hit-testing, animation manually
- ❌ **Accessibility** — Canvas is not accessible (screen readers can't read content)
- ❌ **Text rendering** — Must implement text wrapping, fonts
- ❌ **Event handling** — Must implement click detection manually

**Verdict:** ❌ Too much work — we're trying to save time, not spend more.

---

## Option 6: LeaderLine (Connection Lines)

**GitHub:** <https://github.com/anseki/leader-line>  
**Demo:** <https://anseki.github.io/leader-line/>

### Overview

Library for drawing SVG leader lines between HTML elements.

### Pros

- ✅ **CDN available**
- ✅ **Beautiful animated lines**
- ✅ **No dependencies**
- ✅ **Lightweight** — ~35KB

### Use Case

Perfect companion to Muuri for drawing connections between post-its:

```javascript
// Draw line from exercise post-it to formula post-it
new LeaderLine(
  document.getElementById('postit-1'),
  document.getElementById('postit-2'),
  {
    color: '#4285F4',
    size: 2,
    path: 'straight',
    startPlug: 'disc',
    endPlug: 'arrow3',
    dash: { animation: true }
  }
);
```

**Verdict:** ⭐ **RECOMMENDED as add-on** — Use with Muuri for connection lines.

---

## Option 7: Panzoom (Pan/Zoom Library)

**GitHub:** <https://github.com/timmywil/panzoom>  
**Demo:** <https://timmywil.com/panzoom/demo/>

### Overview

Library for panning and zooming elements.

### Pros

- ✅ **CDN available**
- ✅ **Lightweight** — ~10KB
- ✅ **No dependencies**
- ✅ **Smooth pan/zoom**

### Use Case

Add pan/zoom to the Muuri canvas:

```javascript
const elem = document.querySelector('.whiteboard-canvas');
const panzoom = Panzoom(elem, {
  maxScale: 2,
  minScale: 0.5,
  canvas: true
});

// Enable mouse wheel zoom
elem.parentElement.addEventListener('wheel', panzoom.zoomWithWheel);
```

**Verdict:** ⭐ **RECOMMENDED as add-on** — For v2 interactive canvas.

---

## Final Recommendation: Muuri + LeaderLine

### Stack

1. **Muuri** — Layout engine, animations, responsive grid
2. **LeaderLine** — Connection lines between related notes
3. **Custom CSS** — Post-it styling (colors, rotation, shadows, pins)

### Why This Stack?

- ✅ **No build step** — Works in vanilla HTML via CDN
- ✅ **Lightweight** — ~60KB total (Muuri 25KB + LeaderLine 35KB)
- ✅ **Fast to implement** — 1-2 days vs 1 week custom
- ✅ **Proven** — Production-ready libraries
- ✅ **Customizable** — Full control over visual design
- ✅ **Animations** — Beautiful built-in FLIP animations
- ✅ **v2 ready** — Can add Panzoom + drag later

### CDN Integration

```html
<!DOCTYPE html>
<html>
<head>
  <!-- Muuri for layout -->
  <script src="https://cdn.jsdelivr.net/npm/muuri@0.9.5/dist/muuri.min.js"></script>
  
  <!-- LeaderLine for connections -->
  <script src="https://cdn.jsdelivr.net/npm/leader-line@1.0.7/leader-line.min.js"></script>
  
  <style>
    /* Post-it styles */
    .postit-note {
      position: absolute;
      width: 140px;
      min-height: 100px;
      background: #FEFF9C;
      padding: 12px;
      border-radius: 2px;
      box-shadow: 2px 3px 8px rgba(0,0,0,0.15);
      transform: rotate(var(--rotation, 0deg));
    }
    .postit-note.yellow { background: #FEFF9C; }
    .postit-note.blue { background: #7AFcff; }
    /* ... more colors ... */
    
    /* Pin graphic */
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
    }
  </style>
</head>
<body>
  <div class="whiteboard-canvas">
    <!-- Post-its go here -->
  </div>
  
  <script>
    // Initialize Muuri
    const grid = new Muuri('.whiteboard-canvas', {
      layout: { fillGaps: true },
      dragEnabled: false
    });
    
    // Add post-its as they arrive from WebSocket
    function onNewNote(noteData) {
      const el = createPostItElement(noteData);
      grid.add(el);
      
      // If note has connections, draw lines
      if (noteData.connections) {
        noteData.connections.forEach(targetId => {
          new LeaderLine(el, document.getElementById(targetId));
        });
      }
    }
  </script>
</body>
</html>
```

---

## Implementation Plan (Using Muuri + LeaderLine)

### Phase 1: Setup (Day 1)

1. Add Muuri and LeaderLine CDN links to `index.html`
2. Create basic grid container structure
3. Test with static post-it elements

### Phase 2: Post-It Styling (Day 2)

1. Create CSS for post-it notes (colors, rotation, pins)
2. Add entrance animations
3. Test different note types

### Phase 3: Integration (Day 3)

1. Modify WebSocket handler to call `grid.add()` on new notes
2. Map note types to post-it colors
3. Generate random rotation per note

### Phase 4: Connections (Day 4)

1. Add connection metadata to note data structure
2. Draw LeaderLine connections after notes settle
3. Animate line drawing

### Phase 5: Polish (Day 5)

1. Add corkboard/grid background
2. Fine-tune animations
3. Test with all note types

**Total: 5 days** (vs 7 days custom build)

---

## Alternative: If We Want Drag-and-Drop (v2)

For v2 interactive version:

1. Enable `dragEnabled: true` in Muuri
2. Add **Panzoom** for canvas pan/zoom
3. Persist positions to Firestore

---

## Summary

| Option | Best For | Recommendation |
|--------|----------|----------------|
| **Muuri** | Layout, animations, no-build | ⭐ **PRIMARY** |
| **LeaderLine** | Connection lines | ⭐ **ADD-ON** |
| **Panzoom** | Canvas pan/zoom (v2) | ⭐ **V2 ADD-ON** |
| React-Kanban | React projects | ❌ Not suitable |
| Excalidraw | Drawing apps | ❌ Overkill |
| Custom Canvas | Full control | ❌ Too complex |

**Winning combination:** Muuri + LeaderLine + custom CSS = beautiful post-it canvas in 5 days, no build step required.
