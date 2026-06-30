<script lang="ts">
  import {
    buildXTicks,
    buildYTicksFromStep,
    formatMeasureLabel,
    measureLineStats,
    thinTicks,
    timeLabel,
    type MeasureLine,
    type PlotPoint,
    type Series,
    type SeriesPoint,
  } from '$lib/charts';

  interface Props {
    series: Series[];
    format?: (v: number) => string;
    height?: number;
    /** Fixed Y tick step in value units (e.g. 0.2 TC as raw cycles). */
    yTickStep?: number;
    /** Fixed X tick interval in seconds. */
    xTickIntervalSecs?: number;
    /** Override X domain start (unix seconds). */
    timeStart?: number;
    /** Override X domain end (unix seconds). */
    timeEnd?: number;
    /** Enable click-to-draw rate measurement lines. */
    measureTool?: boolean;
  }

  let {
    series = [],
    format = (v) => String(v),
    height = 260,
    yTickStep,
    xTickIntervalSecs,
    timeStart,
    timeEnd,
    measureTool = false,
  }: Props = $props();

  // Fixed internal coordinate space; the SVG scales to its container.
  const W = 760;
  const PAD = { top: 14, right: 14, bottom: 28, left: 64 };

  const allPoints = $derived(series.flatMap((s) => s.points));

  const vMinRaw = $derived(allPoints.length ? Math.min(...allPoints.map((p) => p.v)) : 0);
  const vMaxRaw = $derived(allPoints.length ? Math.max(...allPoints.map((p) => p.v)) : 1);

  const yScale = $derived.by(() => {
    if (yTickStep && yTickStep > 0) {
      return buildYTicksFromStep(vMinRaw, vMaxRaw, yTickStep, { anchorZero: true });
    }
    const vMax = vMaxRaw > 0 ? vMaxRaw * 1.08 : 1;
    return { vMin: 0, vMax, ticks: [0, 0.25, 0.5, 0.75, 1].map((f) => f * vMax) };
  });

  const vMin = $derived(yScale.vMin);
  const vMax = $derived(yScale.vMax);
  const yTicks = $derived(yScale.ticks);

  const tMin = $derived(
    timeStart ?? (allPoints.length ? Math.min(...allPoints.map((p) => p.t)) : 0),
  );
  const tMax = $derived(
    timeEnd ?? (allPoints.length ? Math.max(...allPoints.map((p) => p.t)) : 1),
  );
  const span = $derived(Math.max(1, tMax - tMin));

  const plotW = $derived(W - PAD.left - PAD.right);
  const plotH = $derived(height - PAD.top - PAD.bottom);

  function sx(t: number): number {
    return PAD.left + ((t - tMin) / span) * plotW;
  }
  function sy(v: number): number {
    const range = vMax - vMin;
    if (range <= 0) return PAD.top + plotH;
    return PAD.top + plotH - ((v - vMin) / range) * plotH;
  }

  function plotPointFromEvent(e: MouseEvent): PlotPoint | null {
    const svg = (e.currentTarget as SVGGraphicsElement).ownerSVGElement;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * W;
    const y = ((e.clientY - rect.top) / rect.height) * height;
    if (x < PAD.left || x > W - PAD.right || y < PAD.top || y > PAD.top + plotH) return null;
    const t = tMin + ((x - PAD.left) / plotW) * span;
    const range = vMax - vMin;
    const v = range > 0 ? vMin + ((PAD.top + plotH - y) / plotH) * range : vMin;
    return { t, v };
  }

  function path(points: SeriesPoint[]): string {
    if (!points.length) return '';
    return points
      .slice()
      .sort((a, b) => a.t - b.t)
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${sx(p.t).toFixed(1)},${sy(p.v).toFixed(1)}`)
      .join(' ');
  }

  const uniqueTimestamps = $derived(
    [...new Set(allPoints.map((p) => p.t))].sort((a, b) => a - b),
  );

  function nearestTs(svgX: number): number | null {
    if (!uniqueTimestamps.length) return null;
    const t = tMin + ((svgX - PAD.left) / plotW) * span;
    let best = uniqueTimestamps[0];
    let bestDist = Math.abs(best - t);
    for (const ts of uniqueTimestamps) {
      const dist = Math.abs(ts - t);
      if (dist < bestDist) {
        best = ts;
        bestDist = dist;
      }
    }
    return best;
  }

  function formatHoverTime(ts: number): string {
    return new Date(ts * 1000).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  let hoverTs = $state<number | null>(null);
  let tooltipX = $state(0);
  let tooltipY = $state(0);
  let chartEl: HTMLDivElement | undefined = $state();

  let measureActive = $state(false);
  let measureDraftStart = $state<PlotPoint | null>(null);
  let measurePreviewEnd = $state<PlotPoint | null>(null);
  let measureLines = $state<MeasureLine[]>([]);
  let measureId = $state(0);

  const measureDraftLine = $derived.by(() => {
    if (!measureDraftStart || !measurePreviewEnd) return null;
    return { a: measureDraftStart, b: measurePreviewEnd };
  });

  function clearMeasureDraft() {
    measureDraftStart = null;
    measurePreviewEnd = null;
  }

  function clearMeasureLines() {
    measureLines = [];
    clearMeasureDraft();
  }

  function toggleMeasure() {
    measureActive = !measureActive;
    clearMeasureDraft();
    hoverTs = null;
  }

  function handleMove(e: MouseEvent) {
    if (measureActive) {
      if (measureDraftStart) {
        measurePreviewEnd = plotPointFromEvent(e);
      }
      return;
    }
    const svg = (e.currentTarget as SVGGraphicsElement).ownerSVGElement;
    if (!svg || !chartEl) return;
    const svgRect = svg.getBoundingClientRect();
    const svgX = ((e.clientX - svgRect.left) / svgRect.width) * W;
    if (svgX < PAD.left || svgX > W - PAD.right) {
      hoverTs = null;
      return;
    }
    hoverTs = nearestTs(svgX);
    const chartRect = chartEl.getBoundingClientRect();
    tooltipX = e.clientX - chartRect.left;
    tooltipY = e.clientY - chartRect.top;
  }

  function handleLeave() {
    if (measureActive) return;
    hoverTs = null;
  }

  function handleClick(e: MouseEvent) {
    if (!measureActive) return;
    e.preventDefault();
    const pt = plotPointFromEvent(e);
    if (!pt) return;
    if (!measureDraftStart) {
      measureDraftStart = pt;
      measurePreviewEnd = pt;
      return;
    }
    measureLines = [...measureLines, { id: measureId++, a: measureDraftStart, b: pt }];
    clearMeasureDraft();
  }

  function handleMeasureKeydown(e: KeyboardEvent) {
    if (!measureActive) return;
    if (e.key === 'Escape') {
      if (measureDraftStart) clearMeasureDraft();
      else measureActive = false;
    }
  }

  const hoverEntries = $derived.by(() => {
    if (hoverTs === null || measureActive) return [];
    return series
      .map((s) => {
        const p = s.points.find((pt) => pt.t === hoverTs);
        return p ? { name: s.name, color: s.color, v: p.v } : null;
      })
      .filter((e): e is { name: string; color: string; v: number } => e !== null);
  });

  const xTicks = $derived.by(() => {
    if (!allPoints.length) return [];
    if (xTickIntervalSecs && xTickIntervalSecs > 0) {
      return buildXTicks(tMin, tMax, xTickIntervalSecs);
    }
    return Array.from({ length: 5 }, (_, i) => tMin + (span * i) / 4);
  });

  const xLabelTicks = $derived(
    thinTicks(xTicks, Math.max(4, Math.floor(plotW / 56))),
  );

  function renderMeasureLine(line: { a: PlotPoint; b: PlotPoint }, dashed: boolean) {
    return {
      x1: sx(line.a.t),
      y1: sy(line.a.v),
      x2: sx(line.b.t),
      y2: sy(line.b.v),
      mx: (sx(line.a.t) + sx(line.b.t)) / 2,
      my: (sy(line.a.v) + sy(line.b.v)) / 2,
      stats: measureLineStats(line.a, line.b),
      dashed,
    };
  }
</script>

<svelte:window onkeydown={handleMeasureKeydown} />

<div class="w-full relative" bind:this={chartEl}>
  {#if measureTool && allPoints.length}
    <div class="flex flex-wrap items-center gap-2 mb-2 px-1">
      <button
        type="button"
        class="px-2.5 py-1 text-xs font-medium rounded-md border {measureActive
          ? 'bg-indigo-600 text-white border-indigo-600'
          : 'bg-white text-primary-700 border-[var(--color-border-primary)] hover:bg-primary-50'}"
        onclick={toggleMeasure}
      >
        {measureActive ? 'Measuring…' : 'Measure rate'}
      </button>
      {#if measureLines.length}
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium rounded-md border border-[var(--color-border-primary)] bg-white text-primary-600 hover:bg-primary-50"
          onclick={clearMeasureLines}
        >
          Clear lines
        </button>
      {/if}
      {#if measureActive}
        <span class="text-xs text-primary-400">
          {#if measureDraftStart}
            Click the second point · Esc cancels
          {:else}
            Click two points to draw a rate line · Esc exits
          {/if}
        </span>
      {/if}
    </div>
  {/if}

  {#if !allPoints.length}
    <div class="flex items-center justify-center text-sm text-primary-400" style="height:{height}px">
      No samples in this range yet.
    </div>
  {:else}
    <svg viewBox="0 0 {W} {height}" class="w-full" style="height:auto" role="img" aria-label="Cycles over time">
      <!-- vertical gridlines -->
      {#each xTicks as t, i (i)}
        <line
          x1={sx(t)}
          x2={sx(t)}
          y1={PAD.top}
          y2={PAD.top + plotH}
          stroke="currentColor"
          class="text-primary-100"
          stroke-width="1"
        />
      {/each}
      <!-- horizontal gridlines + y labels -->
      {#each yTicks as tick, i (i)}
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={sy(tick)}
          y2={sy(tick)}
          stroke="currentColor"
          class="text-primary-100"
          stroke-width="1"
        />
        <text x={PAD.left - 8} y={sy(tick) + 3} text-anchor="end" class="fill-primary-400" font-size="10">
          {format(tick)}
        </text>
      {/each}
      <!-- x labels -->
      {#each xLabelTicks as t, i (i)}
        <text x={sx(t)} y={height - 10} text-anchor="middle" class="fill-primary-400" font-size="10">
          {timeLabel(t, span, xTickIntervalSecs)}
        </text>
      {/each}
      <!-- series lines -->
      {#each series as s (s.name)}
        <path d={path(s.points)} fill="none" stroke={s.color} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
      {/each}
      <!-- data points -->
      {#each series as s (s.name + '-pts')}
        {#each s.points as p (p.t)}
          <circle
            cx={sx(p.t)}
            cy={sy(p.v)}
            r={hoverTs === p.t ? 0 : 3}
            fill={s.color}
            stroke="white"
            stroke-width="1"
            pointer-events="none"
            opacity={hoverTs !== null && hoverTs !== p.t ? 0.35 : 1}
          />
        {/each}
      {/each}
      <!-- committed measure lines -->
      {#each measureLines as ml (ml.id)}
        {@const m = renderMeasureLine(ml, false)}
        {@const label = formatMeasureLabel(m.stats.dt, m.stats.dv, m.stats.ratePerSec, format)}
        <line
          x1={m.x1}
          y1={m.y1}
          x2={m.x2}
          y2={m.y2}
          stroke="#6366f1"
          stroke-width="2"
          stroke-linecap="round"
          pointer-events="none"
        />
        <circle cx={m.x1} cy={m.y1} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" pointer-events="none" />
        <circle cx={m.x2} cy={m.y2} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" pointer-events="none" />
        <rect
          x={m.mx - 180}
          y={m.my - 22}
          width="360"
          height="28"
          rx="4"
          fill="white"
          fill-opacity="0.92"
          stroke="#c7d2fe"
          pointer-events="none"
        />
        <text x={m.mx} y={m.my - 10} text-anchor="middle" class="fill-primary-900" font-size="10" font-weight="600" pointer-events="none">
          {label.primary}
        </text>
        <text x={m.mx} y={m.my + 2} text-anchor="middle" class="fill-primary-500" font-size="9" pointer-events="none">
          {label.secondary}
        </text>
      {/each}
      <!-- draft measure line -->
      {#if measureDraftLine}
        {@const m = renderMeasureLine(measureDraftLine, true)}
        {@const label = formatMeasureLabel(m.stats.dt, m.stats.dv, m.stats.ratePerSec, format)}
        <line
          x1={m.x1}
          y1={m.y1}
          x2={m.x2}
          y2={m.y2}
          stroke="#6366f1"
          stroke-width="2"
          stroke-dasharray="6 4"
          stroke-linecap="round"
          pointer-events="none"
        />
        <circle cx={m.x1} cy={m.y1} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" pointer-events="none" />
        <circle cx={m.x2} cy={m.y2} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" pointer-events="none" />
        <rect
          x={m.mx - 180}
          y={m.my - 22}
          width="360"
          height="28"
          rx="4"
          fill="white"
          fill-opacity="0.92"
          stroke="#c7d2fe"
          pointer-events="none"
        />
        <text x={m.mx} y={m.my - 10} text-anchor="middle" class="fill-primary-900" font-size="10" font-weight="600" pointer-events="none">
          {label.primary}
        </text>
        <text x={m.mx} y={m.my + 2} text-anchor="middle" class="fill-primary-500" font-size="9" pointer-events="none">
          {label.secondary}
        </text>
      {/if}
      <!-- hover crosshair -->
      {#if hoverTs !== null && !measureActive}
        <line
          x1={sx(hoverTs)}
          x2={sx(hoverTs)}
          y1={PAD.top}
          y2={PAD.top + plotH}
          stroke="currentColor"
          class="text-primary-300"
          stroke-width="1"
          stroke-dasharray="4 3"
          pointer-events="none"
        />
        {#each series as s (s.name + '-hover')}
          {#each s.points.filter((p) => p.t === hoverTs) as p (p.t)}
            <circle
              cx={sx(p.t)}
              cy={sy(p.v)}
              r="5"
              fill={s.color}
              stroke="white"
              stroke-width="2"
              pointer-events="none"
            />
          {/each}
        {/each}
      {/if}
      <!-- mouse capture overlay -->
      <rect
        x={PAD.left}
        y={PAD.top}
        width={plotW}
        height={plotH}
        fill="transparent"
        role="presentation"
        class={measureActive ? 'cursor-crosshair' : 'cursor-crosshair'}
        onmousemove={handleMove}
        onmouseleave={handleLeave}
        onclick={handleClick}
      />
    </svg>

    {#if hoverTs !== null && hoverEntries.length}
      <div
        class="absolute z-10 pointer-events-none rounded-md border border-[var(--color-border-primary)] bg-white px-2.5 py-1.5 shadow-md text-xs"
        style="left:{Math.min(tooltipX + 12, (chartEl?.clientWidth ?? 0) - 180)}px; top:{Math.max(tooltipY - 8, 0)}px; transform:translateY(-100%)"
      >
        <div class="font-medium text-primary-900 mb-1">{formatHoverTime(hoverTs)}</div>
        <div class="flex flex-col gap-0.5 max-h-40 overflow-y-auto">
          {#each hoverEntries as e (e.name)}
            <div class="flex items-center gap-1.5 text-primary-600">
              <span class="inline-block w-2 h-2 rounded-full shrink-0" style="background:{e.color}"></span>
              <span class="truncate max-w-[140px]" title={e.name}>{e.name}</span>
              <span class="ml-auto font-medium text-primary-900 tabular-nums">{format(e.v)}</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- legend -->
    <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 px-1">
      {#each series as s (s.name)}
        <div class="flex items-center gap-1.5 text-xs text-primary-600">
          <span class="inline-block w-3 h-1.5 rounded-sm" style="background:{s.color}"></span>
          <span class="truncate max-w-[160px]" title={s.name}>{s.name}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>
