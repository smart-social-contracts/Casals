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

  // 4 horizontal gridlines.
  const yTicks = $derived([0, 0.25, 0.5, 0.75, 1].map((f) => ({ f, v: vMax * f })));
  // Up to 6 time ticks across the span.
  const xTicks = $derived(
    allPoints.length
      ? Array.from({ length: 5 }, (_, i) => tMin + (span * i) / 4)
      : [],
  );
</script>

<div class="w-full">
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
      <!-- series -->
      {#each series as s (s.name)}
        <path d={path(s.points)} fill="none" stroke={s.color} stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        {#if s.points.length === 1}
          <circle cx={sx(s.points[0].t)} cy={sy(s.points[0].v)} r="3" fill={s.color} />
        {/if}
      {/each}
    </svg>

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
