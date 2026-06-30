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

/** Balance row used when summing canister history into one line. */
export interface BalanceSample {
  ts: number;
  canister_id: string;
  cycles: number;
}

/**
 * Sort by time and collapse duplicate timestamps (last sample wins).
 * Required for Lightweight Charts, which needs strictly increasing unique times.
 */
export function dedupeSeriesPoints(points: SeriesPoint[]): SeriesPoint[] {
  const sorted = [...points].sort((a, b) => a.t - b.t);
  const out: SeriesPoint[] = [];
  for (const p of sorted) {
    if (out.length && out[out.length - 1].t === p.t) {
      out[out.length - 1] = p;
    } else {
      out.push({ t: p.t, v: p.v });
    }
  }
  return out;
}

/**
 * Sum balances across canisters at each sample time using forward-fill.
 * Without this, a partial refresh (one canister sampled alone) under-counts
 * the total and draws sharp bogus dips on aggregated lines.
 */
export function aggregateBalanceSeries(samples: BalanceSample[]): SeriesPoint[] {
  if (!samples.length) return [];
  const byCan = new Map<string, SeriesPoint[]>();
  for (const s of samples) {
    let pts = byCan.get(s.canister_id);
    if (!pts) {
      pts = [];
      byCan.set(s.canister_id, pts);
    }
    pts.push({ t: s.ts, v: s.cycles });
  }

  const perCan: SeriesPoint[][] = [];
  const allTs = new Set<number>();
  for (const pts of byCan.values()) {
    const deduped = dedupeSeriesPoints(pts);
    if (!deduped.length) continue;
    perCan.push(deduped);
    for (const p of deduped) allTs.add(p.t);
  }
  if (!perCan.length) return [];

  const times = [...allTs].sort((a, b) => a - b);
  const idx = perCan.map(() => 0);
  const lastV = perCan.map(() => 0);
  const out: SeriesPoint[] = [];

  for (const t of times) {
    let sum = 0;
    for (let c = 0; c < perCan.length; c++) {
      const canPts = perCan[c];
      while (idx[c] < canPts.length && canPts[idx[c]].t <= t) {
        lastV[c] = canPts[idx[c]].v;
        idx[c]++;
      }
      sum += lastV[c];
    }
    out.push({ t, v: sum });
  }
  return out;
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

/** Fixed Y-axis step for cycles balance charts (0.2 TC in raw cycles). */
export const TC_Y_TICK_STEP = 0.2 * 1e12;

export type ChartWindowKey = '1h' | '1d' | '1w' | '1month' | 'inception';

const NICE_X_INTERVALS_SECS = [
  60,
  2 * 60,
  5 * 60,
  10 * 60,
  15 * 60,
  30 * 60,
  3600,
  2 * 3600,
  6 * 3600,
  12 * 3600,
  86400,
  2 * 86400,
  7 * 86400,
  14 * 86400,
  30 * 86400,
  90 * 86400,
  180 * 86400,
  365 * 86400,
];

/** Pick a readable X tick interval for long/inception ranges (~8 ticks). */
export function dynamicXTickInterval(spanSecs: number): number {
  if (spanSecs <= 0) return 3600;
  const target = spanSecs / 8;
  for (const interval of NICE_X_INTERVALS_SECS) {
    if (interval >= target) return interval;
  }
  return NICE_X_INTERVALS_SECS[NICE_X_INTERVALS_SECS.length - 1];
}

export function xTickIntervalForWindow(window: ChartWindowKey, spanSecs: number): number {
  switch (window) {
    case '1h':
      return 60;
    case '1d':
      return 3600;
    case '1w':
      return 6 * 3600;
    case '1month':
      return 86400;
    case 'inception':
      return dynamicXTickInterval(spanSecs);
  }
}

export function buildXTicks(tStart: number, tEnd: number, intervalSecs: number): number[] {
  if (intervalSecs <= 0 || tEnd <= tStart) return [];
  const first = Math.ceil(tStart / intervalSecs) * intervalSecs;
  const ticks: number[] = [];
  for (let t = first; t <= tEnd; t += intervalSecs) {
    ticks.push(t);
  }
  return ticks;
}

/** Keep grid density but cap axis labels so they do not overlap (~56px per label). */
export function thinTicks(ticks: number[], maxCount: number): number[] {
  if (ticks.length <= maxCount || maxCount < 2) return ticks;
  const step = Math.ceil(ticks.length / maxCount);
  const out: number[] = [];
  for (let i = 0; i < ticks.length; i += step) {
    out.push(ticks[i]);
  }
  const last = ticks[ticks.length - 1];
  if (out[out.length - 1] !== last) out.push(last);
  return out;
}

export function buildYTicksFromStep(
  vMinRaw: number,
  vMaxRaw: number,
  step: number,
  options?: { anchorZero?: boolean },
): { vMin: number; vMax: number; ticks: number[] } {
  if (step <= 0) {
    const vMax = vMaxRaw > 0 ? vMaxRaw * 1.08 : 1;
    return { vMin: 0, vMax, ticks: [0, 0.25, 0.5, 0.75, 1].map((f) => f * vMax) };
  }
  const vMin = options?.anchorZero ? 0 : Math.floor(vMinRaw / step) * step;
  let vMax = Math.ceil(vMaxRaw / step) * step;
  if (vMax <= vMin) vMax = vMin + step;
  const ticks: number[] = [];
  for (let v = vMin; v <= vMax + step * 0.001; v += step) {
    ticks.push(v);
  }
  return { vMin, vMax, ticks };
}

// Compact, human time label for a unix-seconds tick given the visible span.
export function timeLabel(tsSecs: number, spanSecs: number, tickIntervalSecs?: number): string {
  const d = new Date(tsSecs * 1000);
  const interval = tickIntervalSecs ?? 0;
  if (interval <= 3600 || spanSecs <= 2 * 3600) {
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }
  if (interval <= 86400 || spanSecs <= 3 * 86400) {
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
    });
  }
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export interface PlotPoint {
  t: number;
  v: number;
}

export interface MeasureLine {
  id: number;
  a: PlotPoint;
  b: PlotPoint;
}

export function measureLineStats(a: PlotPoint, b: PlotPoint): {
  dt: number;
  dv: number;
  ratePerSec: number;
} {
  const dt = b.t - a.t;
  const dv = b.v - a.v;
  const ratePerSec = dt !== 0 ? dv / dt : 0;
  return { dt, dv, ratePerSec };
}

export function formatChartDuration(secs: number): string {
  const abs = Math.abs(secs);
  if (abs < 90) return `${Math.round(abs)}s`;
  if (abs < 5400) return `${(abs / 60).toFixed(abs < 600 ? 1 : 0)}m`;
  if (abs < 172800) return `${(abs / 3600).toFixed(abs < 7200 ? 1 : 0)}h`;
  return `${(abs / 86400).toFixed(abs < 604800 ? 1 : 0)}d`;
}

function formatSignedTcRate(tcPerHour: number, multiplier: number, suffix: string): string {
  const value = tcPerHour * multiplier;
  const sign = value >= 0 ? '+' : '';
  return `${sign}${value.toLocaleString(undefined, { maximumFractionDigits: 4 })} ${suffix}`;
}

export function formatMeasureLabel(
  dt: number,
  dv: number,
  ratePerSec: number,
  formatValue: (v: number) => string,
): { primary: string; secondary: string } {
  const tcPerHour = (ratePerSec * 3600) / 1e12;
  const deltaSign = dv >= 0 ? '+' : '−';
  return {
    primary: [
      formatSignedTcRate(tcPerHour, 1, 'TC/h'),
      formatSignedTcRate(tcPerHour, 24, 'TC/day'),
      formatSignedTcRate(tcPerHour, 24 * 30, 'TC/month'),
    ].join(' · '),
    secondary: `${deltaSign}${formatValue(Math.abs(dv))} · ${formatChartDuration(dt)}`,
  };
}

export const CYCLES_TO_TC = 1 / 1e12;
export const TC_TO_CYCLES = 1e12;

export function cyclesToTc(cycles: number): number {
  return cycles * CYCLES_TO_TC;
}

export function tcToCycles(tc: number): number {
  return tc * TC_TO_CYCLES;
}

export interface ChartEventMarker {
  time: number;
  text: string;
  color: string;
  /** Match ``Series.name`` when set; otherwise show on every series. */
  seriesName?: string;
}

export const CHART_EVENT_BTYPES = new Set([
  'cycles_topup',
  'cycles_return',
  'treasury_spent',
  'cycles_icp_convert',
  'treasury_cycles_deposit',
]);

export function snapPlotPointToSeries(
  series: Series[],
  point: PlotPoint,
  timeToX: (t: number) => number | null,
  valueToY: (v: number) => number | null,
  thresholdPx = 20,
): PlotPoint {
  const px = timeToX(point.t);
  const py = valueToY(point.v);
  if (px === null || py === null) return point;
  let best: PlotPoint | null = null;
  let bestDist = thresholdPx;
  for (const s of series) {
    for (const p of s.points) {
      const x = timeToX(p.t);
      const y = valueToY(p.v);
      if (x === null || y === null) continue;
      const dist = Math.hypot(x - px, y - py);
      if (dist < bestDist) {
        bestDist = dist;
        best = p;
      }
    }
  }
  return best ?? point;
}

export function exportSeriesCsv(series: Series[], visibleNames: Set<string>): string {
  const lines = ['series,unix_secs,iso_time,cycles,tc'];
  for (const s of series) {
    if (!visibleNames.has(s.name)) continue;
    for (const p of [...s.points].sort((a, b) => a.t - b.t)) {
      lines.push(
        `${JSON.stringify(s.name)},${p.t},${new Date(p.t * 1000).toISOString()},${p.v},${cyclesToTc(p.v)}`,
      );
    }
  }
  return lines.join('\n');
}

export function downloadTextFile(filename: string, text: string, mime = 'text/csv;charset=utf-8'): void {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const CHART_MARKER_META: Record<string, { text: string; color: string }> = {
  cycles_topup: { text: '↑', color: '#10b981' },
  cycles_return: { text: '↓', color: '#f59e0b' },
  treasury_spent: { text: '¢', color: '#ef4444' },
  cycles_icp_convert: { text: '⇄', color: '#6366f1' },
  treasury_cycles_deposit: { text: '+', color: '#14b8a6' },
};

export function orchestrationEventToMarker(
  e: { btype: string; timestamp_secs: number; canister_id?: string },
  canisterName?: string,
): ChartEventMarker | null {
  const meta = CHART_MARKER_META[e.btype];
  if (!meta) return null;
  return {
    time: e.timestamp_secs,
    text: meta.text,
    color: meta.color,
    seriesName: canisterName,
  };
}
