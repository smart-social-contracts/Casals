<script lang="ts">
  import { timeLabel, type Series, type SeriesPoint } from '$lib/charts';

  interface Props {
    series: Series[];
    format?: (v: number) => string;
    height?: number;
  }

  let { series = [], format = (v) => String(v), height = 260 }: Props = $props();

  // Fixed internal coordinate space; the SVG scales to its container.
  const W = 760;
  const PAD = { top: 14, right: 14, bottom: 28, left: 64 };

  const allPoints = $derived(series.flatMap((s) => s.points));

  const tMin = $derived(allPoints.length ? Math.min(...allPoints.map((p) => p.t)) : 0);
  const tMax = $derived(allPoints.length ? Math.max(...allPoints.map((p) => p.t)) : 1);
  const vMaxRaw = $derived(allPoints.length ? Math.max(...allPoints.map((p) => p.v)) : 1);
  const vMax = $derived(vMaxRaw > 0 ? vMaxRaw * 1.08 : 1);
  const span = $derived(Math.max(1, tMax - tMin));

  const plotW = $derived(W - PAD.left - PAD.right);
  const plotH = $derived(height - PAD.top - PAD.bottom);

  function sx(t: number): number {
    return PAD.left + ((t - tMin) / span) * plotW;
  }
  function sy(v: number): number {
    return PAD.top + plotH - (v / vMax) * plotH;
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

  function handleMove(e: MouseEvent) {
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
    hoverTs = null;
  }

  const hoverEntries = $derived.by(() => {
    if (hoverTs === null) return [];
    return series
      .map((s) => {
        const p = s.points.find((pt) => pt.t === hoverTs);
        return p ? { name: s.name, color: s.color, v: p.v } : null;
      })
      .filter((e): e is { name: string; color: string; v: number } => e !== null);
  });

  // 4 horizontal gridlines.
  const yTicks = $derived([0, 0.25, 0.5, 0.75, 1].map((f) => ({ f, v: vMax * f })));
  // Up to 6 time ticks across the span.
  const xTicks = $derived(
    allPoints.length
      ? Array.from({ length: 5 }, (_, i) => tMin + (span * i) / 4)
      : [],
  );
</script>

<div class="w-full relative" bind:this={chartEl}>
  {#if !allPoints.length}
    <div class="flex items-center justify-center text-sm text-primary-400" style="height:{height}px">
      No samples in this range yet.
    </div>
  {:else}
    <svg viewBox="0 0 {W} {height}" class="w-full" style="height:auto" role="img" aria-label="Cycles over time">
      <!-- gridlines + y labels -->
      {#each yTicks as tick (tick.f)}
        <line x1={PAD.left} x2={W - PAD.right} y1={sy(tick.v)} y2={sy(tick.v)} stroke="currentColor" class="text-primary-100" stroke-width="1" />
        <text x={PAD.left - 8} y={sy(tick.v) + 3} text-anchor="end" class="fill-primary-400" font-size="10">{format(tick.v)}</text>
      {/each}
      <!-- x labels -->
      {#each xTicks as t, i (i)}
        <text x={sx(t)} y={height - 10} text-anchor="middle" class="fill-primary-400" font-size="10">{timeLabel(t, span)}</text>
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
      <!-- hover crosshair -->
      {#if hoverTs !== null}
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
        class="cursor-crosshair"
        onmousemove={handleMove}
        onmouseleave={handleLeave}
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
