// Squarified treemap (Bruls, Huizing, van Wijk). No other imports so node:test
// can load it without the Svelte / agent graph that charts.ts pulls in.

export interface TreemapInput {
  name: string;
  /** Area of this node. Layout uses this, not balance or burn. */
  value: number;
  /** Cycles held now (live balance when the page has one). */
  balance?: number;
  /** Cycles burned between the diagram's period start and end. */
  burn?: number;
  section?: string;
  stand?: string;
  canister_id?: string;
  children?: TreemapInput[];
}

export interface TreemapRect {
  name: string;
  value: number;
  x: number;
  y: number;
  w: number;
  h: number;
  depth: number; // 1 = section, 2 = stand, 3 = canister
  data: TreemapInput;
}

// Worst aspect ratio of a candidate row (Bruls et al. squarified treemap).
function worstRatio(areas: number[], side: number, sumArea: number): number {
  if (side <= 0 || sumArea <= 0) return Infinity;
  const thickness = sumArea / side;
  let worst = 0;
  for (const a of areas) {
    const cell = a / thickness;
    if (cell <= 0) continue;
    const ratio = Math.max(thickness / cell, cell / thickness);
    if (ratio > worst) worst = ratio;
  }
  return worst || Infinity;
}

// Lay a node's children out within a rect using the squarified algorithm.
// Returns rects in the same order as `nodes`.
function layoutChildren(
  nodes: TreemapInput[],
  x: number,
  y: number,
  w: number,
  h: number,
): { x: number; y: number; w: number; h: number }[] {
  const out: { x: number; y: number; w: number; h: number }[] = nodes.map(() => ({ x, y, w: 0, h: 0 }));
  const total = nodes.reduce((s, n) => s + Math.max(0, n.value), 0);
  if (total <= 0 || w <= 0 || h <= 0) return out;

  const areaScale = (w * h) / total;
  const items = nodes
    .map((n, i) => ({ i, area: Math.max(0, n.value) * areaScale }))
    .sort((a, b) => b.area - a.area);

  let rx = x, ry = y, rw = w, rh = h;
  let idx = 0;
  while (idx < items.length) {
    const vertical = rw < rh; // fill a row along the shorter side
    const sideLen = vertical ? rw : rh;

    let row: { i: number; area: number }[] = [];
    let rowArea = 0;
    let bestWorst = Infinity;
    let k = idx;
    while (k < items.length) {
      const candArea = rowArea + items[k].area;
      const wst = worstRatio([...row, items[k]].map((c) => c.area), sideLen, candArea);
      if (row.length === 0 || wst <= bestWorst) {
        row = [...row, items[k]];
        rowArea = candArea;
        bestWorst = wst;
        k++;
      } else break;
    }

    const thickness = sideLen > 0 ? rowArea / sideLen : 0;
    let pos = vertical ? rx : ry;
    for (const it of row) {
      const cellSide = rowArea > 0 ? (it.area / rowArea) * sideLen : 0;
      if (vertical) {
        out[it.i] = { x: pos, y: ry, w: cellSide, h: thickness };
      } else {
        out[it.i] = { x: rx, y: pos, w: thickness, h: cellSide };
      }
      pos += cellSide;
    }

    if (vertical) { ry += thickness; rh -= thickness; }
    else { rx += thickness; rw -= thickness; }
    idx += row.length;
  }
  return out;
}

// Padding is applied again at the stand inside a section. A fixed 4px inset
// on a short cell (the demo's Website section is only ~20px tall) leaves the
// canister as a dash inside an empty frame. Shrink the inset with the cell.
function levelInset(w: number, h: number, depth: number): { pad: number; header: number } {
  if (depth === 0) return { pad: 0, header: 0 };
  const minSide = Math.min(Math.max(0, w), Math.max(0, h));
  const pad = minSide >= 48 ? 4 : minSide >= 28 ? 2 : minSide >= 16 ? 1 : 0;
  let header = 0;
  if (w > 60 && h - 2 * pad > 28) {
    header = Math.min(15, Math.floor((h - 2 * pad) * 0.35));
  }
  return { pad, header };
}

// Recursively lay out a nested treemap. The root itself is not emitted; its
// descendants are, each tagged with a depth so the renderer can draw section
// frames, stand frames and canister tiles. `pad`/`header` inset each level so
// nesting is visible.
export function treemapLayout(root: TreemapInput, width: number, height: number): TreemapRect[] {
  const out: TreemapRect[] = [];

  function recurse(node: TreemapInput, x: number, y: number, w: number, h: number, depth: number) {
    const children = node.children;
    if (!children || !children.length) return;
    const { pad, header } = levelInset(w, h, depth);
    const ix = x + pad;
    const iy = y + pad + header;
    const iw = Math.max(0, w - 2 * pad);
    const ih = Math.max(0, h - 2 * pad - header);

    const kids = [...children].sort((a, b) => b.value - a.value);
    const rects = layoutChildren(kids, ix, iy, iw, ih);
    kids.forEach((kid, i) => {
      const r = rects[i];
      out.push({ name: kid.name, value: kid.value, ...r, depth: depth + 1, data: kid });
      recurse(kid, r.x, r.y, r.w, r.h, depth + 1);
    });
  }

  recurse(root, 0, 0, width, height, 0);
  return out;
}
