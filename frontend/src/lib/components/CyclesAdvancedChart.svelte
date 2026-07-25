<script lang="ts">
  import { untrack, type Snippet } from 'svelte';
  import {
    createChart,
    CrosshairMode,
    LineType,
    LineSeries,
    HistogramSeries,
    ColorType,
    type IChartApi,
    type ISeriesApi,
    type UTCTimestamp,
  } from 'lightweight-charts';
  import {
    cyclesToTc,
    downloadTextFile,
    exportSeriesCsv,
    formatMeasureLabel,
    measureLineStats,
    paddedTcPriceRange,
    snapPlotPointToSeries,
    dedupeSeriesPoints,
    tcToCycles,
    valueNearTime,
    visibleTcRange,
    CHART_MARKER_LEGEND,
    buildDateAxisBands,
    formatAxisClock,
    formatAxisDateShort,
    formatBarBucketLabel,
    formatBucketRangeLabel,
    axisTickInterval,
    iterAlignedAxisTicks,
    formatAxisTick,
    thinDateBandsByPixel,
    iterLocalHourTicks,
    iterLocalDayStarts,
    thinTicksByPixel,
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
    toolbar?: Snippet;
    measureActive?: boolean;
    snapToData?: boolean;
    isExpanded?: boolean;
    measureOverlayVisible?: boolean;
    showLegend?: boolean;
    variant?: 'line' | 'histogram';
    barBucketSecs?: number;
  }

  let {
    series = [],
    format = (v) => String(v),
    height = 320,
    timeStart,
    timeEnd,
    events = [],
    toolbar,
    measureActive = $bindable(false),
    snapToData = $bindable(true),
    isExpanded = $bindable(false),
    measureOverlayVisible = $bindable(false),
    showLegend = true,
    variant = 'line',
    barBucketSecs,
  }: Props = $props();

  type ChartSeriesApi = ISeriesApi<'Line'> | ISeriesApi<'Histogram'>;

  let containerEl: HTMLDivElement | undefined = $state();
  let plotWrapEl: HTMLDivElement | undefined = $state();
  let panelEl: HTMLDivElement | undefined = $state();

  let chart: IChartApi | null = null;
  let refSeries: ChartSeriesApi | null = null;
  let appliedRange: { from: number; to: number } | null = null;
  const seriesMap = new Map<string, ChartSeriesApi>();

  let visible = $state<Set<string>>(new Set());
  let visibleSeriesKey = '';
  let measureDraftStart = $state<PlotPoint | null>(null);
  let measurePreviewEnd = $state<PlotPoint | null>(null);
  let measureLines = $state<MeasureLine[]>([]);
  let measureId = $state(0);
  let overlaySize = $state({ w: 0, h: height });
  let overlayVersion = $state(0);
  let viewportH = $state(typeof window !== 'undefined' ? window.innerHeight : 800);
  let hoveredMarker = $state<ChartEventMarker | null>(null);
  let hoverPos = $state<{ x: number; y: number } | null>(null);
  let crosshairAxisLabel = $state<{ x: number; label: string } | null>(null);
  let barSelection = $state<{
    px: number;
    py: number;
    cycles: number;
    startTs: number;
    endTs: number;
  } | null>(null);

  const MARKER_RADIUS = 9;
  const MARKER_HIT_RADIUS = 14;
  const BAR_HIT_RADIUS_PX = 18;
  const TIME_AXIS_HEIGHT = 44;
  const MAX_HOUR_GRID_SPAN_SECS = 60 * 86400;

  let axisVersion = $state(0);
  let tsPlotWidth = $state(0);
  let priceScaleWidth = $state(0);
  let paneHeight = $state(0);
  let visibleRangeFrom = $state<number | null>(null);
  let visibleRangeTo = $state<number | null>(null);
  let yAxisUserAdjusted = $state(false);
  let lastFitPriceRange: { from: number; to: number } | null = null;
  let autoFitDataKey = '';
  let fittingYAxis = false;
  let axisLayout = $state({
    hourLines: [] as { x: number }[],
    dayLines: [] as { x: number }[],
    hourLabels: [] as { x: number; label: string }[],
    dateBands: [] as { x0: number; x1: number; cx: number; label: string }[],
  });

  const chartHeight = $derived(
    isExpanded && typeof window !== 'undefined'
      ? Math.max(320, viewportH - 168)
      : height,
  );

  function clearCrosshairAxisLabel() {
    crosshairAxisLabel = null;
  }

  function clearBarSelection() {
    barSelection = null;
  }

  function nearestHistogramBar(x: number): { t: number; v: number; px: number; py: number } | null {
    if (variant !== 'histogram') return null;
    let best: { t: number; v: number; px: number; dist: number } | null = null;
    for (const s of visibleSeries()) {
      for (const p of s.points) {
        const px = timeToX(p.t);
        if (px === null) continue;
        const dist = Math.abs(px - x);
        if (!best || dist < best.dist) {
          best = { t: p.t, v: p.v, px, dist };
        }
      }
    }
    if (!best || best.dist > BAR_HIT_RADIUS_PX) return null;
    const py = valueToY(best.v);
    if (py === null) return null;
    return { t: best.t, v: best.v, px: best.px, py };
  }

  function updateCrosshairAxisLabel(param: { point?: { x: number; y: number }; time?: UTCTimestamp | number }) {
    if (!param.point || !chart) {
      clearCrosshairAxisLabel();
      return;
    }
    let time: number | null = null;
    if (typeof param.time === 'number') {
      time = param.time;
    } else {
      const t = chart.timeScale().coordinateToTime(param.point.x);
      if (typeof t === 'number') time = t;
    }
    if (time === null) {
      clearCrosshairAxisLabel();
      return;
    }

    if (variant === 'histogram' && barBucketSecs) {
      const bar = nearestHistogramBar(param.point.x);
      if (bar) {
        const startTs = bar.t - barBucketSecs;
        crosshairAxisLabel = {
          x: bar.px,
          label: formatBucketRangeLabel(startTs, bar.t),
        };
        return;
      }
    }

    crosshairAxisLabel = {
      x: param.point.x,
      label: formatCrosshairTime(time as UTCTimestamp),
    };
  }

  function selectHistogramBar(x: number) {
    const bar = nearestHistogramBar(x);
    if (!bar || !barBucketSecs) {
      clearBarSelection();
      return;
    }
    if (barSelection && barSelection.endTs === bar.t) {
      clearBarSelection();
      return;
    }
    barSelection = {
      px: bar.px,
      py: bar.py,
      cycles: bar.v,
      startTs: bar.t - barBucketSecs,
      endTs: bar.t,
    };
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
    overlayVersion++;
    rebuildAxisLayout();
  }

  function chartTimeToX(t: number): number | null {
    if (!chart) return null;
    const x = chart.timeScale().timeToCoordinate(t as UTCTimestamp);
    if (x !== null) return x;
    const from = visibleRangeFrom ?? timeStart ?? null;
    const to = visibleRangeTo ?? timeEnd ?? null;
    const plotW = tsPlotWidth || containerEl?.clientWidth || 0;
    if (from == null || to == null || to <= from || plotW <= 0) return null;
    return ((t - from) / (to - from)) * plotW;
  }

  function measurePlotMetrics(): { plotW: number; paneH: number; scaleW: number } {
    if (!chart) {
      return { plotW: plotWrapEl?.clientWidth ?? 0, paneH: 0, scaleW: 0 };
    }
    const scaleW = chart.priceScale('right').width();
    const pane = chart.paneSize();
    let plotW = chart.timeScale().width() || pane.width;
    if (plotW <= 0 && plotWrapEl) {
      plotW = Math.max(0, plotWrapEl.clientWidth - scaleW);
    }
    if (plotW <= 0 && containerEl) {
      plotW = Math.max(0, containerEl.clientWidth - scaleW);
    }
    return { plotW, paneH: pane.height, scaleW };
  }

  function rebuildAxisLayout() {
    if (!chart) return;
    const { plotW, paneH, scaleW } = measurePlotMetrics();
    tsPlotWidth = plotW;
    priceScaleWidth = scaleW;
    paneHeight = paneH;

    const range = chart.timeScale().getVisibleRange();
    let from = visibleRangeFrom;
    let to = visibleRangeTo;
    if (range && typeof range.from === 'number' && typeof range.to === 'number') {
      from = range.from;
      to = range.to;
      visibleRangeFrom = from;
      visibleRangeTo = to;
    } else if (from == null || to == null) {
      from = timeStart ?? null;
      to = timeEnd ?? null;
    }
    if (from == null || to == null || to <= from) {
      axisLayout = { hourLines: [], dayLines: [], hourLabels: [], dateBands: [] };
      axisVersion++;
      return;
    }

    let axisPlotW = plotW;
    if (axisPlotW <= 0 && containerEl) {
      axisPlotW = Math.max(0, containerEl.clientWidth - scaleW);
      tsPlotWidth = axisPlotW;
    }
    if (axisPlotW <= 0) {
      axisLayout = { hourLines: [], dayLines: [], hourLabels: [], dateBands: [] };
      axisVersion++;
      return;
    }

    const span = to - from;
    let hourLines: { x: number }[] = [];
    let dayLines: { x: number }[] = [];
    let hourLabels: { x: number; label: string }[] = [];
    let dateBands: { x0: number; x1: number; cx: number; label: string }[] = [];

    if (variant === 'histogram') {
      const bucketSecs = barBucketSecs ?? 86400;
      const barTimes = [
        ...new Set(visibleSeries().flatMap((s) => s.points.map((p) => p.t))),
      ].sort((a, b) => a - b);
      hourLabels = thinTicksByPixel(barTimes, chartTimeToX, 56)
        .map((t) => {
          const x = chartTimeToX(t);
          if (x === null) return null;
          return { x, label: formatBarBucketLabel(t, bucketSecs) };
        })
        .filter((row): row is { x: number; label: string } => row !== null)
        .sort((a, b) => a.x - b.x);
    } else {
      const tickInterval = axisTickInterval(span, axisPlotW);
      const longSpan = span > 2 * 86400;
      const axisTicks = iterAlignedAxisTicks(from, to, longSpan ? Math.max(tickInterval, 86400) : tickInterval);
      const showHourGrid = tickInterval <= 3600 && span <= MAX_HOUR_GRID_SPAN_SECS;
      const hours = showHourGrid ? iterLocalHourTicks(from, to) : [];
      const days = iterLocalDayStarts(from, to);

      if (showHourGrid) {
        for (const t of hours) {
          const x = chartTimeToX(t);
          if (x !== null && x >= -1 && x <= axisPlotW + 1) hourLines.push({ x });
        }
      }

      for (const t of days) {
        const x = chartTimeToX(t);
        if (x !== null && x >= -1 && x <= axisPlotW + 1) dayLines.push({ x });
      }

      hourLabels = thinTicksByPixel(axisTicks, chartTimeToX, longSpan ? 64 : 76)
        .map((t) => {
          const x = chartTimeToX(t);
          if (x === null) return null;
          const label = longSpan
            ? formatAxisDateShort(t)
            : formatAxisTick(t, tickInterval, span);
          return { x, label };
        })
        .filter((row): row is { x: number; label: string } => row !== null);

      dateBands = longSpan
        ? []
        : thinDateBandsByPixel(
          buildDateAxisBands(from, to)
            .map((b) => {
              const x0 = chartTimeToX(b.startTs) ?? 0;
              const x1 = chartTimeToX(b.endTs) ?? axisPlotW;
              return {
                x0,
                x1,
                cx: (x0 + x1) / 2,
                label: b.label,
              };
            })
            .filter((b) => b.x1 > b.x0 + 12),
        );
    }

    axisLayout = { hourLines, dayLines, hourLabels, dateBands };
    axisVersion++;
  }

  function clearMarkerHover() {
    hoveredMarker = null;
    hoverPos = null;
    clearCrosshairAxisLabel();
  }

  interface MarkerPlacement {
    event: ChartEventMarker;
    x: number;
    y: number;
  }

  function markerTargetsForEvent(e: ChartEventMarker, vis: Series[]): Series[] {
    if (!vis.length) return [];
    if (e.seriesName) {
      const exact = vis.filter((s) => s.name === e.seriesName);
      if (exact.length) return exact;
      return [vis[0]];
    }
    return [vis[0]];
  }

  function markerPlacements(): MarkerPlacement[] {
    if (!chart) return [];
    const out: MarkerPlacement[] = [];
    const vis = visibleSeries();
    for (const e of events) {
      for (const s of markerTargetsForEvent(e, vis)) {
        const v = valueNearTime(s.points, e.time);
        if (v === null) continue;
        const ls = seriesMap.get(s.name);
        const x = timeToX(e.time);
        const y = ls?.priceToCoordinate(cyclesToTc(v)) ?? valueToY(v);
        if (x === null || y === null) continue;
        out.push({ event: e, x, y: y - MARKER_RADIUS - 2 });
      }
    }
    return out;
  }

  function handleChartMouseMove(e: MouseEvent) {
    if (!containerEl || measureActive) {
      clearMarkerHover();
      return;
    }
    const rect = containerEl.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    let best: (MarkerPlacement & { dist: number }) | null = null;
    for (const m of markerPlacements()) {
      const dist = Math.hypot(m.x - mx, m.y - my);
      if (dist <= MARKER_HIT_RADIUS && (!best || dist < best.dist)) {
        best = { ...m, dist };
      }
    }
    if (best) {
      hoveredMarker = best.event;
      hoverPos = { x: best.x, y: best.y };
    } else {
      clearMarkerHover();
    }
  }

  function toggleExpanded() {
    isExpanded = !isExpanded;
    if (chart && containerEl) {
      requestAnimationFrame(() => {
        if (!chart || !containerEl) return;
        const h = isExpanded ? containerEl.clientHeight || chartHeight : chartHeight;
        chart.applyOptions({ height: h, width: containerEl.clientWidth });
        resizeOverlay(true);
        bumpOverlay();
      });
    }
  }

  function collapseExpanded() {
    if (!isExpanded) return;
    isExpanded = false;
    if (chart && containerEl) {
      requestAnimationFrame(() => {
        if (!chart || !containerEl) return;
        chart.applyOptions({ height: chartHeight, width: containerEl.clientWidth });
        resizeOverlay(true);
        bumpOverlay();
      });
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

  export function toggleMeasure() {
    measureActive = !measureActive;
    clearMeasureDraft();
  }

  export function toggleSnapToData() {
    snapToData = !snapToData;
  }

  function checkPriceRangeUserChange() {
    if (!chart || fittingYAxis || !lastFitPriceRange || yAxisUserAdjusted) return;
    const range = chart.priceScale('right').getVisibleRange();
    if (!range) return;
    const span = Math.max(lastFitPriceRange.to - lastFitPriceRange.from, 1e-12);
    const eps = span * 0.02;
    if (
      Math.abs(range.from - lastFitPriceRange.from) > eps
      || Math.abs(range.to - lastFitPriceRange.to) > eps
    ) {
      yAxisUserAdjusted = true;
    }
  }

  export function fitYAxis() {
    if (!chart) return;
    const range = visibleTcRange(series, visible);
    if (!range) return;
    const { minValue, maxValue } = paddedTcPriceRange(range.min, range.max);
    fittingYAxis = true;
    chart.priceScale('right').applyOptions({ autoScale: false });
    chart.priceScale('right').setVisibleRange({ from: minValue, to: maxValue });
    lastFitPriceRange = { from: minValue, to: maxValue };
    yAxisUserAdjusted = false;
    requestAnimationFrame(() => {
      fittingYAxis = false;
    });
    bumpOverlay();
  }

  function chartDataFitKey(): string {
    return series.map((s) => `${s.name}:${s.points.length}:${s.points.at(-1)?.t ?? ''}:${s.points.at(-1)?.v ?? ''}`).join('|');
  }

  function maybeAutoFitYAxis(force = false) {
    const key = chartDataFitKey();
    if (key !== autoFitDataKey) {
      autoFitDataKey = key;
      yAxisUserAdjusted = false;
      force = true;
    }
    if (force || !yAxisUserAdjusted) fitYAxis();
  }

  export { clearMeasureLines, toggleExpanded, exportCsv };

  function toggleSeries(name: string) {
    const next = new Set(visible);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    visible = next;
    seriesMap.get(name)?.applyOptions({ visible: next.has(name) });
    pickRefSeries();
    yAxisUserAdjusted = false;
    fitYAxis();
  }

  function pickRefSeries() {
    refSeries = [...seriesMap.values()].find((ls) => ls.options().visible !== false) ?? null;
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

  function handleChartClick(param: { point?: { x: number; y: number } }) {
    if (measureActive) {
      handleMeasureClick(param);
      return;
    }
    if (variant === 'histogram' && param.point) {
      selectHistogramBar(param.point.x);
    }
  }

  function handleCrosshairMove(param: {
    point?: { x: number; y: number };
    time?: UTCTimestamp | number;
  }) {
    handleMeasureMove(param);
    updateCrosshairAxisLabel(param);
  }

  function handleMeasureMove(param: { point?: { x: number; y: number } }) {
    if (!measureActive || !measureDraftStart || !param.point) return;
    const raw = coordToPlot(param.point.x, param.point.y);
    if (raw) {
      measurePreviewEnd = applyPlotPoint(raw);
      overlayVersion++;
    }
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

    for (const [name, ls] of [...seriesMap]) {
      if (!names.has(name)) {
        chart.removeSeries(ls);
        seriesMap.delete(name);
      }
    }

    for (const s of series) {
      let ls = seriesMap.get(s.name);
      const data = sortedLineData(s.points);
      if (!ls) {
        if (variant === 'histogram') {
          ls = chart.addSeries(HistogramSeries, {
            color: s.color,
            lastValueVisible: false,
            priceLineVisible: false,
            visible: visible.has(s.name),
          });
        } else {
          ls = chart.addSeries(LineSeries, {
            color: s.color,
            lineWidth: 2,
            lineType: LineType.WithSteps,
            crosshairMarkerVisible: true,
            lastValueVisible: false,
            priceLineVisible: false,
            visible: visible.has(s.name),
          });
        }
        seriesMap.set(s.name, ls);
      }
      ls.applyOptions({ color: s.color, visible: visible.has(s.name) });
      ls.setData(data);
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
    bumpOverlay();
    maybeAutoFitYAxis();
    requestAnimationFrame(() => {
      rebuildAxisLayout();
      requestAnimationFrame(() => rebuildAxisLayout());
    });
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
      if (isExpanded) {
        collapseExpanded();
        return;
      }
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

  let scaleInteractionCleanup: (() => void) | null = null;

  function destroyChart() {
    try {
      scaleInteractionCleanup?.();
    } catch {
      // Chart DOM may already be gone when series unmounts during window changes.
    }
    scaleInteractionCleanup = null;
    if (!chart) return;
    chart.remove();
    chart = null;
    seriesMap.clear();
    refSeries = null;
    appliedRange = null;
  }

  function createChartInstance() {
    if (!containerEl || chart) return;

    const onClick = (param: { point?: { x: number; y: number } }) => handleChartClick(param);
    const onMove = (param: {
      point?: { x: number; y: number };
      time?: UTCTimestamp | number;
    }) => handleCrosshairMove(param);
    const onRange = () => {
      bumpOverlay();
      checkPriceRangeUserChange();
    };
    const onScaleInteraction = () => {
      requestAnimationFrame(() => checkPriceRangeUserChange());
    };

    const c = createChart(containerEl, {
      height: plotWrapEl?.clientHeight || Math.max(200, chartHeight - TIME_AXIS_HEIGHT),
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#64748b',
        fontSize: 11,
      },
      grid: {
        vertLines: { visible: false },
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
        visible: false,
        borderVisible: false,
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
    const chartNode = containerEl;
    chartNode.addEventListener('wheel', onScaleInteraction, { passive: true });
    chartNode.addEventListener('pointerup', onScaleInteraction);
    scaleInteractionCleanup = () => {
      chartNode.removeEventListener('wheel', onScaleInteraction);
      chartNode.removeEventListener('pointerup', onScaleInteraction);
    };
  }

  // Mount / remount chart when the container or height changes.
  $effect(() => {
    if (!containerEl || !plotWrapEl) return;
    chartHeight;
    isExpanded;
    variant;

    destroyChart();
    createChartInstance();

    const plotNode = plotWrapEl;
    const chartNode = containerEl;
    const resizeObs = new ResizeObserver(() => {
      if (!chart || !chartNode || !plotNode) return;
      const h = plotNode.clientHeight || chartHeight - TIME_AXIS_HEIGHT;
      chart.applyOptions({ width: chartNode.clientWidth, height: h });
      untrack(() => {
        resizeOverlay();
        rebuildAxisLayout();
      });
    });
    resizeObs.observe(plotNode);

    untrack(() => {
      syncChartData();
      rebuildAxisLayout();
    });

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
    barBucketSecs;
    if (!chart) return;
    untrack(() => syncChartData());
  });
  $effect(() => {
    measureOverlayVisible = measureLines.length > 0 || measureDraftStart !== null;
  });
  $effect(() => {
    variant;
    barBucketSecs;
    if (variant !== 'histogram') clearBarSelection();
  });

  // Lock page scroll while the chart overlay is open; resize with the window.
  $effect(() => {
    if (!isExpanded || typeof document === 'undefined') return;
    document.body.style.overflow = 'hidden';
    viewportH = window.innerHeight;
    const onResize = () => {
      viewportH = window.innerHeight;
      if (chart && containerEl) {
        chart.applyOptions({
          height: containerEl.clientHeight || chartHeight,
          width: containerEl.clientWidth,
        });
        untrack(() => resizeOverlay(true));
        untrack(() => bumpOverlay());
      }
    };
    window.addEventListener('resize', onResize);
    return () => {
      document.body.style.overflow = '';
      window.removeEventListener('resize', onResize);
    };
  });
</script>

<svelte:window onkeydown={handleMeasureKeydown} />

<div class="w-full">
  {#if !series.length}
    <div class="flex items-center justify-center text-sm text-primary-400" style="height:{height}px">
      No samples in this range yet.
    </div>
  {:else}
    <div
      bind:this={panelEl}
      class="cycles-chart-panel rounded-xl {isExpanded
        ? 'fixed inset-0 z-50 flex flex-col bg-white p-4 overflow-hidden'
        : ''}"
    >
      {#if isExpanded && toolbar}
        <div class="shrink-0 mb-2">
          {@render toolbar()}
        </div>
      {/if}

      <div
        class="rounded-lg overflow-hidden border border-[var(--color-border-primary)]/60 flex flex-col {isExpanded ? 'flex-1 min-h-0' : ''}"
        style={isExpanded ? undefined : `height:${chartHeight}px`}
      >
        <div
          class="relative flex-1 min-h-0"
          bind:this={plotWrapEl}
          onmousemove={handleChartMouseMove}
          onmouseleave={clearMarkerHover}
          role="presentation"
        >
          <div bind:this={containerEl} class="absolute inset-0 w-full h-full"></div>
          {#if tsPlotWidth > 0 && paneHeight > 0}
            {#key axisVersion}
              <svg
                class="absolute top-0 left-0 pointer-events-none"
                width={tsPlotWidth}
                height={paneHeight}
                aria-hidden="true"
              >
                {#each axisLayout.hourLines as h, i (i)}
                  <line x1={h.x} y1="0" x2={h.x} y2={paneHeight} stroke="#eef2f7" stroke-width="1" />
                {/each}
                {#each axisLayout.dayLines as d, i (i)}
                  <line x1={d.x} y1="0" x2={d.x} y2={paneHeight} stroke="#cbd5e1" stroke-width="1.5" />
                {/each}
              </svg>
            {/key}
          {/if}
        {#if events.length}
          {#key overlayVersion}
            <svg
              class="absolute inset-0 w-full h-full pointer-events-none"
              width={overlaySize.w}
              height={overlaySize.h}
              aria-hidden="true"
            >
              {#each markerPlacements() as m (`${m.event.time}-${m.event.btype}-${m.event.seriesName ?? 'all'}-${m.x}`)}
                {@const active = hoveredMarker === m.event}
                <circle
                  cx={m.x}
                  cy={m.y}
                  r={active ? MARKER_RADIUS + 1.5 : MARKER_RADIUS}
                  fill={m.event.color}
                  stroke="white"
                  stroke-width="2"
                />
                <text
                  x={m.x}
                  y={m.y + 4}
                  text-anchor="middle"
                  fill="white"
                  font-size="11"
                  font-weight="700"
                >{m.event.text}</text>
              {/each}
            </svg>
          {/key}
        {/if}
        {#if barSelection}
          <div
            class="absolute z-20 pointer-events-none max-w-[280px] rounded-md border border-[var(--color-border-primary)] bg-white/95 px-2.5 py-1.5 shadow-md text-left"
            style="left:{barSelection.px}px; top:{barSelection.py}px; transform: translate(-50%, calc(-100% - 10px));"
          >
            <p class="text-[11px] font-semibold text-primary-900 leading-snug">{format(barSelection.cycles)}</p>
            <p class="text-[10px] text-primary-500 mt-0.5">{formatBucketRangeLabel(barSelection.startTs, barSelection.endTs)}</p>
          </div>
        {/if}
        {#if hoveredMarker && hoverPos}
          <div
            class="absolute z-20 pointer-events-none max-w-[240px] rounded-md border border-[var(--color-border-primary)] bg-white/95 px-2.5 py-1.5 shadow-md text-left"
            style="left:{hoverPos.x}px; top:{hoverPos.y}px; transform: translate(-50%, calc(-100% - 10px));"
          >
            <p class="text-[11px] font-semibold text-primary-900 leading-snug">{hoveredMarker.title}</p>
            {#if hoveredMarker.detail}
              <p class="text-[10px] text-primary-600 mt-0.5">{hoveredMarker.detail}</p>
            {/if}
            <p class="text-[10px] text-primary-400 mt-0.5">{formatCrosshairTime(hoveredMarker.time as UTCTimestamp)}</p>
          </div>
        {/if}
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
        <div class="flex shrink-0 border-t border-slate-200 bg-white" style="height:{TIME_AXIS_HEIGHT}px">
          <div
            class="relative overflow-hidden shrink-0"
            style="width:{Math.max(tsPlotWidth, plotWrapEl?.clientWidth ? Math.max(0, plotWrapEl.clientWidth - priceScaleWidth) : 0)}px; height:{TIME_AXIS_HEIGHT}px"
          >
            {#if tsPlotWidth > 0 || axisLayout.hourLabels.length > 0 || axisLayout.dateBands.length > 0}
              {#key axisVersion}
                <svg width={tsPlotWidth} height={TIME_AXIS_HEIGHT} aria-hidden="true">
                  {#each axisLayout.hourLines as h, i (i)}
                    <line x1={h.x} y1="0" x2={h.x} y2="10" stroke="#cbd5e1" stroke-width="1" />
                  {/each}
                  {#each axisLayout.dayLines as d, i (i)}
                    <line x1={d.x} y1="0" x2={d.x} y2={TIME_AXIS_HEIGHT} stroke="#94a3b8" stroke-width="1.25" />
                  {/each}
                  {#each axisLayout.hourLabels as l (l.x + l.label)}
                    {#if variant === 'histogram'}
                      <line x1={l.x} y1="0" x2={l.x} y2="8" stroke="#cbd5e1" stroke-width="1" />
                    {/if}
                    <text x={l.x} y="16" text-anchor="middle" fill="#64748b" font-size="10">{l.label}</text>
                  {/each}
                  {#each axisLayout.dateBands as b (b.label + b.x0)}
                    <text x={b.cx} y="32" text-anchor="middle" fill="#64748b" font-size="10">{b.label}</text>
                  {/each}
                </svg>
              {/key}
            {/if}
            {#if crosshairAxisLabel}
              <div
                class="absolute z-10 pointer-events-none rounded px-1.5 py-0.5 text-[10px] font-medium text-white whitespace-nowrap"
                style="left:{crosshairAxisLabel.x}px; top:22px; transform: translateX(-50%); background:#334155;"
              >
                {crosshairAxisLabel.label}
              </div>
            {/if}
          </div>
          <div class="shrink-0" style="width:{priceScaleWidth}px"></div>
        </div>
      </div>

      {#if showLegend}
      <div class="flex flex-wrap gap-x-3 gap-y-1.5 mt-2 px-1 shrink-0">
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
      {/if}
      {#if events.length}
        <div class="flex flex-wrap gap-x-4 gap-y-1.5 mt-2 px-1 text-[11px] text-primary-600 shrink-0">
          <span class="text-primary-400 mr-1">Activity markers:</span>
          {#each CHART_MARKER_LEGEND as item (item.btype)}
            <span class="inline-flex items-center gap-1.5">
              <span
                class="inline-flex items-center justify-center w-5 h-5 rounded-full text-[11px] font-bold text-white shrink-0"
                style="background:{item.color}"
              >{item.text}</span>
              {item.label}
            </span>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
</div>
