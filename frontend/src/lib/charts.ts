// Tiny dependency-free charting helpers (SVG-rendered in LineChart/Treemap).
//
// We hand-roll these instead of pulling in a charting library: the data is
// small (a handful of canisters, a few hundred samples) and the static adapter
// + Svelte 5 make inline SVG the simplest, lightest option.

export const PALETTE = [
  '#6366f1', // indigo
  '#10b981', // emerald
  '#f59e0b', // amber
  '#ef4444', // red
  '#3b82f6', // blue
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#14b8a6', // teal
  '#f97316', // orange
  '#84cc16', // lime
];

export function colorAt(i: number): string {
  return PALETTE[((i % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

export interface SeriesPoint {
  t: number; // unix seconds
  v: number;
}

export interface Series {
  name: string;
  color: string;
  points: SeriesPoint[];
}

export interface TreemapInput {
  name: string;
  value: number;
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

// Recursively lay out a nested treemap. The root itself is not emitted; its
// descendants are, each tagged with a depth so the renderer can draw section
// frames, stand frames and canister tiles. `pad`/`header` inset each level so
// nesting is visible.
export function treemapLayout(root: TreemapInput, width: number, height: number): TreemapRect[] {
  const out: TreemapRect[] = [];

  function recurse(node: TreemapInput, x: number, y: number, w: number, h: number, depth: number) {
    const children = node.children;
    if (!children || !children.length) return;
    const pad = depth === 0 ? 0 : 4;
    const header = depth === 0 ? 0 : w > 56 && h > 30 ? 15 : 0;
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

// Compact, human time label for a unix-seconds tick given the visible span.
export function timeLabel(tsSecs: number, spanSecs: number): string {
  const d = new Date(tsSecs * 1000);
  if (spanSecs <= 36 * 3600) {
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
