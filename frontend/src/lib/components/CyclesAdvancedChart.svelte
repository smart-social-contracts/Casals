<script lang="ts">
  import { untrack } from 'svelte';
  import {
    createChart,
    CrosshairMode,
    LineType,
    ColorType,
    type IChartApi,
    type ISeriesApi,
    type SeriesMarker,
    type UTCTimestamp,
  } from 'lightweight-charts';
  import {
    cyclesToTc,
    downloadTextFile,
    exportSeriesCsv,
    formatMeasureLabel,
    measureLineStats,
    snapPlotPointToSeries,
    dedupeSeriesPoints,
    tcToCycles,
    timeLabel,
    type ChartEventMarker,
    type MeasureLine,
    type PlotPoint,
    type Series,
  } from '$lib/charts';

  interface Props {
    series?: Series[];
    format?: (v: number) => string;
    height?: number;
    timeStart?: number;
    timeEnd?: number;
    events?: ChartEventMarker[];
  }

  let {
    series = [],
    format = (v) => String(v),
    height = 320,
    timeStart,
    timeEnd,
    events = [],
  }: Props = $props();

  let containerEl: HTMLDivElement | undefined = $state();
  let panelEl: HTMLDivElement | undefined = $state();

  let chart: IChartApi | null = null;
  let refSeries: ISeriesApi<'Line'> | null = null;
  let appliedRange: { from: number; to: number } | null = null;
  const lineSeriesMap = new Map<string, ISeriesApi<'Line'>>();

  let visible = $state<Set<string>>(new Set());
  let visibleSeriesKey = '';
  let measureActive = $state(false);
  let snapToData = $state(true);
  let measureDraftStart = $state<PlotPoint | null>(null);
  let measurePreviewEnd = $state<PlotPoint | null>(null);
  let measureLines = $state<MeasureLine[]>([]);
  let measureId = $state(0);
  let overlaySize = $state({ w: 0, h: height });
  let overlayVersion = $state(0);
  let isFullscreen = $state(false);

  const chartHeight = $derived(
    isFullscreen && typeof window !== 'undefined'
      ? Math.max(420, window.innerHeight - 200)
      : height,
  );

  function formatAxisTime(ts: number): string {
    const span = Math.max(1, (timeEnd ?? 0) - (timeStart ?? 0));
    return timeLabel(ts, span);
  }

  function formatCrosshairTime(ts: UTCTimestamp): string {
    const d = new Date(Number(ts) * 1000);
    const span = Math.max(1, (timeEnd ?? 0) - (timeStart ?? 0));
    if (span <= 2 * 3600) {
      return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }
    if (span <= 3 * 86400) {
      return d.toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    }
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  }

  function bumpOverlay() {
    if (!measureLines.length && !measureDraftStart) return;
    overlayVersion++;
  }

  function onFullscreenChange() {
    isFullscreen = document.fullscreenElement === panelEl;
    if (chart && containerEl) {
      chart.applyOptions({ height: chartHeight, width: containerEl.clientWidth });
      resizeOverlay(true);
    }
  }

  async function toggleFullscreen() {
    if (!panelEl) return;
    try {
      if (document.fullscreenElement === panelEl) {
        await document.exitFullscreen();
      } else {
        await panelEl.requestFullscreen();
      }
    } catch {
      // Fullscreen API unavailable or denied.
    }
  }

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
    overlayVersion++;
  }

  function toggleMeasure() {
    measureActive = !measureActive;
    clearMeasureDraft();
  }

  function toggleSeries(name: string) {
    const next = new Set(visible);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    visible = next;
    lineSeriesMap.get(name)?.applyOptions({ visible: next.has(name) });
    pickRefSeries();
  }

  function pickRefSeries() {
    refSeries = [...lineSeriesMap.values()].find((ls) => ls.options().visible !== false) ?? null;
  }

  function visibleSeries(): Series[] {
    return series.filter((s) => visible.has(s.name));
  }

  function timeToX(t: number): number | null {
    if (!chart) return null;
    return chart.timeScale().timeToCoordinate(t as UTCTimestamp);
  }

  function valueToY(v: number): number | null {
    if (!refSeries) return null;
    return refSeries.priceToCoordinate(cyclesToTc(v));
  }

  function coordToPlot(x: number, y: number): PlotPoint | null {
    if (!chart || !refSeries) return null;
    const time = chart.timeScale().coordinateToTime(x);
    if (time === null || typeof time !== 'number') return null;
    const price = refSeries.coordinateToPrice(y);
    if (price === null) return null;
    return { t: time, v: tcToCycles(price) };
  }

  function plotToCoord(p: PlotPoint): { x: number; y: number } | null {
    const x = timeToX(p.t);
    const y = valueToY(p.v);
    if (x === null || y === null) return null;
    return { x, y };
  }

  function applyPlotPoint(raw: PlotPoint): PlotPoint {
    if (!snapToData) return raw;
    return snapPlotPointToSeries(visibleSeries(), raw, timeToX, valueToY);
  }

  function handleMeasureClick(param: { point?: { x: number; y: number } }) {
    if (!measureActive || !param.point) return;
    const raw = coordToPlot(param.point.x, param.point.y);
    if (!raw) return;
    const pt = applyPlotPoint(raw);
    if (!measureDraftStart) {
      measureDraftStart = pt;
      measurePreviewEnd = pt;
      return;
    }
    measureLines = [...measureLines, { id: measureId++, a: measureDraftStart, b: pt }];
    clearMeasureDraft();
    overlayVersion++;
  }

  function handleMeasureMove(param: { point?: { x: number; y: number } }) {
    if (!measureActive || !measureDraftStart || !param.point) return;
    const raw = coordToPlot(param.point.x, param.point.y);
    if (raw) {
      measurePreviewEnd = applyPlotPoint(raw);
      overlayVersion++;
    }
  }

  function markersForSeries(name: string): SeriesMarker<UTCTimestamp>[] {
    return events
      .filter((e) => !e.seriesName || e.seriesName === name)
      .map((e) => ({
        time: e.time as UTCTimestamp,
        position: 'aboveBar' as const,
        color: e.color,
        shape: 'circle' as const,
        text: e.text,
        size: 0.5,
      }));
  }

  function sortedLineData(points: Series['points']) {
    return dedupeSeriesPoints(points).map((p) => ({
      time: p.t as UTCTimestamp,
      value: cyclesToTc(p.v),
    }));
  }

  function ensureVisibleNames(names: Set<string>) {
    const key = [...names].sort().join('\0');
    if (key === visibleSeriesKey) return;
    visibleSeriesKey = key;
    visible = new Set(names);
  }

  function syncChartData() {
    if (!chart) return;
    const names = new Set(series.map((s) => s.name));
    ensureVisibleNames(names);

    for (const [name, ls] of [...lineSeriesMap]) {
      if (!names.has(name)) {
        chart.removeSeries(ls);
        lineSeriesMap.delete(name);
      }
    }

    for (const s of series) {
      let ls = lineSeriesMap.get(s.name);
      if (!ls) {
        ls = chart.addLineSeries({
          color: s.color,
          lineWidth: 2,
          lineType: LineType.WithSteps,
          crosshairMarkerVisible: true,
          lastValueVisible: false,
          priceLineVisible: false,
          visible: visible.has(s.name),
          autoscaleInfoProvider: (original) => {
            const base = original();
            if (!base) return base;
            return {
              ...base,
              priceRange: {
                minValue: 0,
                maxValue: Math.max(base.priceRange.maxValue, 0.2),
              },
            };
          },
        });
        lineSeriesMap.set(s.name, ls);
      }
      ls.applyOptions({ color: s.color, visible: visible.has(s.name) });
      ls.setData(sortedLineData(s.points));
      ls.setMarkers(markersForSeries(s.name));
    }

    pickRefSeries();

    if (
      timeStart !== undefined
      && timeEnd !== undefined
      && timeEnd > timeStart
      && (appliedRange?.from !== timeStart || appliedRange?.to !== timeEnd)
    ) {
      chart.timeScale().setVisibleRange({
        from: timeStart as UTCTimestamp,
        to: timeEnd as UTCTimestamp,
      });
      appliedRange = { from: timeStart, to: timeEnd };
    }

    resizeOverlay();
  }

  function resizeOverlay(force = false) {
    if (!containerEl) return;
    const w = containerEl.clientWidth;
    const h = containerEl.clientHeight;
    if (!force && overlaySize.w === w && overlaySize.h === h) return;
    overlaySize = { w, h };
  }

  function exportCsv() {
    const csv = exportSeriesCsv(series, visible);
    downloadTextFile(`cycles-chart-${Date.now()}.csv`, csv);
  }

  function handleMeasureKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      if (measureDraftStart) {
        clearMeasureDraft();
        overlayVersion++;
        return;
      }
      if (measureLines.length) {
        clearMeasureLines();
        return;
      }
      if (measureActive) measureActive = false;
      return;
    }
    if (!measureActive) return;
  }

  function renderMeasureSvg(line: { a: PlotPoint; b: PlotPoint }, dashed: boolean) {
    const a = plotToCoord(line.a);
    const b = plotToCoord(line.b);
    if (!a || !b) return null;
    const stats = measureLineStats(line.a, line.b);
    const label = formatMeasureLabel(stats.dt, stats.dv, stats.ratePerSec, format);
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    return { a, b, label, mx, my, dashed };
  }

  function destroyChart() {
    if (!chart) return;
    chart.remove();
    chart = null;
    lineSeriesMap.clear();
    refSeries = null;
    appliedRange = null;
  }

  function createChartInstance() {
    if (!containerEl || chart) return;

    const onClick = (param: { point?: { x: number; y: number } }) => handleMeasureClick(param);
    const onMove = (param: { point?: { x: number; y: number } }) => handleMeasureMove(param);
    const onRange = () => bumpOverlay();

    const c = createChart(containerEl, {
      height: chartHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#64748b',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#f1f5f9' },
        horzLines: { color: '#f1f5f9' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#cbd5e1', labelBackgroundColor: '#334155' },
        horzLine: { color: '#cbd5e1', labelBackgroundColor: '#334155' },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.08, bottom: 0.05 },
      },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: false,
        fixRightEdge: false,
        tickMarkFormatter: (t) => formatAxisTime(Number(t)),
      },
      localization: {
        locale: typeof navigator !== 'undefined' ? navigator.language : undefined,
        timeFormatter: (t) => formatCrosshairTime(t as UTCTimestamp),
        priceFormatter: (tc: number) =>
          `${tc.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 3 })} TC`,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    });

    chart = c;
    c.subscribeClick(onClick);
    c.subscribeCrosshairMove(onMove);
    c.timeScale().subscribeVisibleTimeRangeChange(onRange);
  }

  // Mount / remount chart when the container or height changes.
  $effect(() => {
    if (!containerEl) return;
    chartHeight;

    destroyChart();
    createChartInstance();

    const node = containerEl;
    const resizeObs = new ResizeObserver(() => {
      if (!chart || !node) return;
      chart.applyOptions({ width: node.clientWidth, height: chartHeight });
      untrack(() => resizeOverlay());
    });
    resizeObs.observe(node);

    untrack(() => syncChartData());

    return () => {
      resizeObs.disconnect();
      destroyChart();
    };
  });

  // Push new props into the chart without touching reactive state.
  $effect(() => {
    series;
    events;
    timeStart;
    timeEnd;
    if (!chart) return;
    untrack(() => syncChartData());
  });
</script>

<svelte:window onkeydown={handleMeasureKeydown} onfullscreenchange={onFullscreenChange} />

<div class="w-full">
  {#if !series.length}
    <div class="flex items-center justify-center text-sm text-primary-400" style="height:{height}px">
      No samples in this range yet.
    </div>
  {:else}
    <div
      bind:this={panelEl}
      class="cycles-chart-panel rounded-xl {isFullscreen ? 'bg-white p-4 min-h-screen flex flex-col' : ''}"
    >
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
        <label class="inline-flex items-center gap-1.5 text-xs text-primary-600 cursor-pointer select-none">
          <input type="checkbox" class="rounded border-primary-300" bind:checked={snapToData} />
          Snap to samples
        </label>
        {#if measureLines.length || measureDraftLine}
          <button
            type="button"
            class="px-2.5 py-1 text-xs font-medium rounded-md border border-[var(--color-border-primary)] bg-white text-primary-600 hover:bg-primary-50"
            onclick={(e) => {
              e.stopPropagation();
              clearMeasureLines();
            }}
          >
            Clear lines
          </button>
        {/if}
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium rounded-md border border-[var(--color-border-primary)] bg-white text-primary-600 hover:bg-primary-50"
          onclick={toggleFullscreen}
          title={isFullscreen ? 'Exit full screen' : 'Full screen'}
        >
          {isFullscreen ? 'Exit full screen' : 'Full screen'}
        </button>
        <button
          type="button"
          class="px-2.5 py-1 text-xs font-medium rounded-md border border-[var(--color-border-primary)] bg-white text-primary-600 hover:bg-primary-50 {isFullscreen ? '' : 'ml-auto'}"
          onclick={exportCsv}
        >
          Export CSV
        </button>
        {#if measureActive}
          <span class="text-xs text-primary-400 w-full sm:w-auto">
            {#if measureDraftStart}
              Click second point · Esc cancels
            {:else}
              Pan/zoom freely · click two points to measure · Esc exits
            {/if}
          </span>
        {/if}
      </div>

      <div class="relative w-full rounded-lg overflow-hidden border border-[var(--color-border-primary)]/60 {isFullscreen ? 'flex-1 min-h-0' : ''}">
        <div bind:this={containerEl} class="w-full" style="height:{chartHeight}px"></div>
        {#if measureLines.length || measureDraftLine}
          {#key overlayVersion}
            <svg
              class="absolute inset-0 pointer-events-none"
              width={overlaySize.w}
              height={overlaySize.h}
              aria-hidden="true"
            >
              {#each measureLines as ml (ml.id)}
                {@const m = renderMeasureSvg(ml, false)}
                {#if m}
                  <line x1={m.a.x} y1={m.a.y} x2={m.b.x} y2={m.b.y} stroke="#6366f1" stroke-width="2" />
                  <circle cx={m.a.x} cy={m.a.y} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" />
                  <circle cx={m.b.x} cy={m.b.y} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" />
                  <rect x={m.mx - 180} y={m.my - 24} width="360" height="32" rx="4" fill="white" fill-opacity="0.94" stroke="#c7d2fe" />
                  <text x={m.mx} y={m.my - 12} text-anchor="middle" fill="#0f172a" font-size="10" font-weight="600">{m.label.primary}</text>
                  <text x={m.mx} y={m.my + 2} text-anchor="middle" fill="#64748b" font-size="9">{m.label.secondary}</text>
                {/if}
              {/each}
              {#if measureDraftLine}
                {@const m = renderMeasureSvg(measureDraftLine, true)}
                {#if m}
                  <line x1={m.a.x} y1={m.a.y} x2={m.b.x} y2={m.b.y} stroke="#6366f1" stroke-width="2" stroke-dasharray="6 4" />
                  <circle cx={m.a.x} cy={m.a.y} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" />
                  <circle cx={m.b.x} cy={m.b.y} r="4.5" fill="#6366f1" stroke="white" stroke-width="1.5" />
                  <rect x={m.mx - 180} y={m.my - 24} width="360" height="32" rx="4" fill="white" fill-opacity="0.94" stroke="#c7d2fe" />
                  <text x={m.mx} y={m.my - 12} text-anchor="middle" fill="#0f172a" font-size="10" font-weight="600">{m.label.primary}</text>
                  <text x={m.mx} y={m.my + 2} text-anchor="middle" fill="#64748b" font-size="9">{m.label.secondary}</text>
                {/if}
              {/if}
            </svg>
          {/key}
        {/if}
      </div>

      <div class="flex flex-wrap gap-x-3 gap-y-1.5 mt-2 px-1">
        {#each series as s (s.name)}
          <button
            type="button"
            class="inline-flex items-center gap-1.5 text-xs rounded-md px-1.5 py-0.5 border transition-colors {visible.has(s.name)
              ? 'text-primary-800 border-[var(--color-border-primary)] bg-white'
              : 'text-primary-400 border-transparent opacity-50 line-through'}"
            onclick={() => toggleSeries(s.name)}
            title={visible.has(s.name) ? 'Hide series' : 'Show series'}
          >
            <span class="inline-block w-3 h-1.5 rounded-sm shrink-0" style="background:{s.color}"></span>
            <span class="truncate max-w-[160px]">{s.name}</span>
          </button>
        {/each}
      </div>
      {#if events.length}
        <p class="text-[11px] text-primary-400 mt-1 px-1">
          Markers: top-up, return, convert, treasury deposit (from Activity).
        </p>
      {/if}
    </div>
  {/if}
</div>
