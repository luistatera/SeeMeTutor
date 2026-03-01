# Single Library vs. Two Libraries — Analysis

**Question:** Is it better to use one solution instead of merging LeaderLine + Muuri?

**Answer:** For v1, use **Muuri only** and draw simple SVG connections ourselves. Add LeaderLine only if needed for v2.

---

## Option A: Muuri Only (RECOMMENDED for v1)

Use Muuri for layout/animation, and draw simple SVG lines manually.

### Why This Works

1. **v1 is non-interactive** — Lines don't need to update dynamically as notes move
2. **Simple connections** — Just straight or slightly curved lines between note centers
3. **One less dependency** — 35KB saved, one less CDN call
4. **Full control** — Own the code, no library quirks

### Implementation

```javascript
// After Muuri layout settles, draw SVG lines
function drawConnections() {
  const svg = document.getElementById('connections-layer');
  
  notes.forEach(note => {
    if (note.connections) {
      note.connections.forEach(targetId => {
        const target = document.getElementById(targetId);
        const line = createSVGLine(note.element, target);
        svg.appendChild(line);
      });
    }
  });
}

function createSVGLine(el1, el2) {
  const rect1 = el1.getBoundingClientRect();
  const rect2 = el2.getBoundingClientRect();
  
  const x1 = rect1.left + rect1.width / 2;
  const y1 = rect1.top + rect1.height / 2;
  const x2 = rect2.left + rect2.width / 2;
  const y2 = rect2.top + rect2.height / 2;
  
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  line.setAttribute('d', `M ${x1} ${y1} L ${x2} ${y2}`);
  line.setAttribute('stroke', '#4285F4');
  line.setAttribute('stroke-width', '2');
  line.setAttribute('stroke-dasharray', '5,5');
  line.setAttribute('marker-end', 'url(#arrowhead)');
  
  return line;
}
```

### SVG Layer CSS

```css
.connections-layer {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}

.connection-line {
  animation: dash-flow 20s linear infinite;
}

@keyframes dash-flow {
  to { stroke-dashoffset: -100; }
}
```

### Pros

- ✅ Single library (Muuri only)
- ✅ ~25KB total (vs 60KB with LeaderLine)
- ✅ Simpler mental model
- ✅ No LeaderLine quirks to work around
- ✅ Easy to animate line drawing

### Cons

- ❌ Lines won't auto-update if notes move (but v1 is static anyway)
- ❌ Must write ~50 lines of SVG code

---

## Option B: Vis.js Network (Single Library Alternative)

**GitHub:** <https://github.com/visjs/vis-network>  
**Demo:** <https://visjs.github.io/vis-network/examples/>

### Overview

Vis.js Network is designed for **node graphs** — exactly what we need (nodes = post-its, edges = connections).

### Pros

- ✅ **One library** — Both layout AND connections
- ✅ **Built-in physics** — Auto-positions nodes to avoid overlap
- ✅ **Built-in edges** — Lines, arrows, labels out of the box
- ✅ **Animations** — Smooth transitions
- ✅ **CDN available** — `https://unpkg.com/vis-network/standalone/umd/vis-network.min.js`

### Cons

- ❌ **Heavy** — ~280KB minified (11x larger than Muuri)
- ❌ **Complex API** — Designed for data visualization, not UI
- ❌ **Canvas-based** — Harder to style post-its with CSS
- ❌ **Physics overhead** — Continuous simulation running

### Usage Example

```javascript
const nodes = new vis.DataSet([
  { id: 1, label: 'Exercise 1', color: '#FEFF9C', shape: 'box' },
  { id: 2, label: 'Formula', color: '#7AFcff', shape: 'box' }
]);

const edges = new vis.DataSet([
  { from: 1, to: 2, arrows: 'to', dashes: true }
]);

const network = new vis.Network(container, { nodes, edges }, {
  physics: { enabled: true },
  interaction: { dragNodes: false } // v1: non-interactive
});
```

**Verdict:** ❌ Too heavy and complex for our use case.

---

## Option C: Cytoscape.js (Graph Theory Library)

**GitHub:** <https://github.com/cytoscape/cytoscape.js>  
**Demo:** <https://js.cytoscape.org/>

### Overview

Industrial-strength graph theory library.

### Pros

- ✅ Very powerful
- ✅ Many layout algorithms

### Cons

- ❌ **Very heavy** — ~300KB+
- ❌ **Steep learning curve**
- ❌ Overkill for simple post-its

**Verdict:** ❌ Not suitable.

---

## Comparison Table

| Approach | Libraries | Size | Complexity | Lines of Code | Recommendation |
|----------|-----------|------|------------|---------------|----------------|
| **Muuri only** | 1 | 25KB | Low | ~100 | ⭐ **v1 BEST** |
| Muuri + LeaderLine | 2 | 60KB | Medium | ~20 | Good for v2 |
| Vis.js Network | 1 | 280KB | High | ~50 | ❌ Too heavy |
| Cytoscape.js | 1 | 300KB | Very High | ~80 | ❌ Overkill |
| Custom Canvas | 0 | 0KB | Very High | ~500 | ❌ Too much work |

---

## Final Recommendation

### For v1 (Non-Interactive, Demo-Only)

**Use Muuri only** — Draw SVG lines manually after layout settles.

**Reasons:**

1. Demo is short (4 minutes) — Notes don't move after placement
2. Lines only need to be drawn once
3. Save 35KB and a CDN call
4. Simpler = fewer bugs during demo
5. 50 lines of SVG code is trivial

### For v2 (Interactive, Post-Hackathon)

**Add LeaderLine** — If you want drag-to-reposition with auto-updating lines.

---

## Implementation: Muuri + Simple SVG

```html
<!DOCTYPE html>
<html>
<head>
  <!-- Only Muuri -->
  <script src="https://cdn.jsdelivr.net/npm/muuri@0.9.5/dist/muuri.min.js"></script>
  
  <style>
    .whiteboard-canvas {
      position: relative;
      width: 100%;
      height: 100%;
    }
    
    /* Post-its */
    .postit-note {
      position: absolute;
      width: 140px;
      padding: 12px;
      background: #FEFF9C;
      border-radius: 2px;
      box-shadow: 2px 3px 8px rgba(0,0,0,0.15);
    }
    
    /* SVG layer for connections */
    .connections-layer {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: 1;
    }
    
    .connection-line {
      fill: none;
      stroke: #4285F4;
      stroke-width: 2;
      stroke-dasharray: 5,5;
      animation: dash-flow 20s linear infinite;
      opacity: 0;
      animation: draw-line 0.5s ease-out forwards;
    }
    
    @keyframes draw-line {
      to { opacity: 1; }
    }
    
    @keyframes dash-flow {
      to { stroke-dashoffset: -100; }
    }
  </style>
</head>
<body>
  <div class="whiteboard-canvas">
    <svg class="connections-layer" id="connections-svg">
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" 
                refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#4285F4" />
        </marker>
      </defs>
    </svg>
    <!-- Post-its go here -->
  </div>
  
  <script>
    // Initialize Muuri
    const grid = new Muuri('.whiteboard-canvas', {
      items: '.postit-note',
      layout: { fillGaps: true }
    });
    
    // Add note
    function addNote(data) {
      const el = createPostItElement(data);
      grid.add(el);
      
      // After Muuri animates, draw connections
      setTimeout(() => drawConnections(data.connections), 500);
    }
    
    // Draw SVG connections
    function drawConnections(connections) {
      if (!connections) return;
      
      const svg = document.getElementById('connections-svg');
      const containerRect = svg.getBoundingClientRect();
      
      connections.forEach(targetId => {
        const source = document.getElementById(connections.sourceId);
        const target = document.getElementById(targetId);
        
        if (!source || !target) return;
        
        const sRect = source.getBoundingClientRect();
        const tRect = target.getBoundingClientRect();
        
        const x1 = sRect.left + sRect.width / 2 - containerRect.left;
        const y1 = sRect.top + sRect.height / 2 - containerRect.top;
        const x2 = tRect.left + tRect.width / 2 - containerRect.left;
        const y2 = tRect.top + tRect.height / 2 - containerRect.top;
        
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', `M ${x1} ${y1} L ${x2} ${y2}`);
        path.setAttribute('class', 'connection-line');
        path.setAttribute('marker-end', 'url(#arrowhead)');
        
        svg.appendChild(path);
      });
    }
  </script>
</body>
</html>
```

---

## Summary

| Question | Answer |
|----------|--------|
| Should we merge two libraries? | **No** — Use Muuri only for v1 |
| Is there a single library that does both? | Yes (Vis.js, Cytoscape) but they're 10x heavier |
| What's the simplest solution? | Muuri + 50 lines of custom SVG |
| When should we add LeaderLine? | v2, if you need interactive drag-and-drop |

**Bottom line:** One library (Muuri) + minimal custom code is better than two libraries OR one heavy library.
