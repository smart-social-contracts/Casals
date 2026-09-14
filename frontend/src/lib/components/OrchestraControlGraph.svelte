<script lang="ts">
  import type { Tree, OrchestrationStatus } from '$lib/api';
  import { shortPrincipal } from '$lib/api';
  import {
    mergeBatonStatus,
    findBatonsInTree,
    resolveBatons,
    isBatonCanister,
    isMultisigCanister,
    isCasalsCanister,
  } from '$lib/orchestraGovernance';
  import {
    buildControlGraph,
    layoutControlGraph,
    edgeAnchors,
    edgePathBetween,
    frozenGraphViewport,
    filterControlGraph,
    filterControlGraphByEdgeTypes,
    layerGroupEnabled,
    setLayerGroupVisibility,
    standVisibilityKey,
    serializeControlGraphView,
    parseControlGraphView,
    clampControlGraphZoom,
    CONTROL_GRAPH_ZOOM_STEP,
    type ControlGraphView,
    CONTROL_NODE_WIDTH,
    CONTROL_NODE_HEIGHT,
    CONTROL_EDGE_META,
    type ControlEdgeType,
    type ControlGraphLayers,
    type ControlEdge,
    type ControlNode,
    type ControlEdgeTypeVisibility,
    type NodePosition,
    DEFAULT_CONTROL_GRAPH_LAYERS,
    DEFAULT_CONTROL_EDGE_TYPE_VISIBILITY,
  } from '$lib/orchestraControlGraph';

  interface Props {
    tree: Tree;
    orchestrationStatus?: OrchestrationStatus | null;
    casalsBackendId?: string;
    principalLabel?: (principal: string) => string;
    onRefreshControllers?: () => Promise<void>;
    controllersRefreshing?: boolean;
  }

  let {
    tree,
    orchestrationStatus = null,
    casalsBackendId = '',
    principalLabel,
    onRefreshControllers,
    controllersRefreshing = false,
  }: Props = $props();

  let edgeTypes = $state<ControlEdgeTypeVisibility>({ ...DEFAULT_CONTROL_EDGE_TYPE_VISIBILITY });
  let hoveredEdge = $state<ControlEdge | null>(null);
  let hoveredNode = $state<ControlNode | null>(null);
  let hoveredLegendType = $state<ControlEdgeType | null>(null);
  let containerWidth = $state(960);
  let customPositions = $state<Record<string, NodePosition>>({});
  let frozenViewport = $state({ minX: 0, minY: 0, width: 720, height: 400 });
  let svgEl = $state<SVGSVGElement | null>(null);
  let draggingId = $state<string | null>(null);
  let isExpanded = $state(false);
  let panelEl = $state<HTMLDivElement | null>(null);
  let graphAreaEl = $state<HTMLDivElement | null>(null);
  let graphAreaWidth = $state(960);
  let visibilityOpen = $state(true);
  let expandedVisibilitySections = $state<Record<string, boolean>>({});

  let hiddenSections = $state<Set<string>>(new Set());
  let hiddenStands = $state<Set<string>>(new Set());
  let hiddenCanisters = $state<Set<string>>(new Set());
  let hiddenPrincipals = $state<Set<string>>(new Set());
  let principalsOpen = $state(true);
  let suppressNodeDblClickUntil = 0;
  let zoom = $state(1);
  let savedViews = $state<Record<string, ControlGraphView>>({});
  let selectedViewName = $state('');
  let viewNameInput = $state('');
  let fileInputEl = $state<HTMLInputElement | null>(null);

  const DRAG_THRESHOLD_PX = 5;
  const VISIBILITY_SIDEBAR_WIDTH = 288;
  const VIEWS_STORAGE_KEY = 'casals.controlGraph.views';

  const batons = $derived(
    mergeBatonStatus(findBatonsInTree(tree), resolveBatons(orchestrationStatus, tree)),
  );

  const fullGraph = $derived(
    buildControlGraph(tree, orchestrationStatus, batons, {
      layers: DEFAULT_CONTROL_GRAPH_LAYERS,
      casalsBackendId,
      principalLabel,
    }),
  );

  const edgeFilteredGraph = $derived(filterControlGraphByEdgeTypes(fullGraph, edgeTypes));

  const principalNodes = $derived(
    edgeFilteredGraph.nodes
      .filter((n) => n.kind === 'principal' && n.principal)
      .sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id)),
  );

  const graph = $derived(
    filterControlGraph(edgeFilteredGraph, {
      hiddenSections,
      hiddenStands,
      hiddenCanisters,
      hiddenPrincipals,
    }),
  );

  const layoutWidth = $derived(
    Math.max(isExpanded ? graphAreaWidth : containerWidth, 720),
  );
  const autoPositions = $derived(layoutControlGraph(graph, layoutWidth));

  // Dragged positions are only discarded by Reset layout or loading a view.
  // In particular a browser zoom changes layoutWidth, and re-laying out there
  // would throw away the arrangement the operator just built.
  $effect(() => {
    frozenViewport = frozenGraphViewport(autoPositions, layoutWidth);
  });

  $effect(() => {
    if (!graphAreaEl) return;
    const ro = new ResizeObserver((entries) => {
      graphAreaWidth = entries[0]?.contentRect.width ?? 960;
    });
    ro.observe(graphAreaEl);
    return () => ro.disconnect();
  });

  $effect(() => {
    if (!isExpanded || typeof document === 'undefined') return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  });

  const displayPositions = $derived.by(() => {
    const out = new Map(autoPositions);
    for (const [id, pos] of Object.entries(customPositions)) {
      out.set(id, pos);
    }
    return out;
  });

  function resetLayout(): void {
    customPositions = {};
    frozenViewport = frozenGraphViewport(autoPositions, layoutWidth);
  }

  function toggleExpanded(): void {
    isExpanded = !isExpanded;
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (event.key === 'Escape' && isExpanded) {
      isExpanded = false;
    }
  }

  function sectionVisible(name: string): boolean {
    return !hiddenSections.has(name);
  }

  function standVisible(section: string, stand: string): boolean {
    return sectionVisible(section) && !hiddenStands.has(standVisibilityKey(section, stand));
  }

  function canisterVisible(section: string, stand: string, canisterId: string): boolean {
    return (
      standVisible(section, stand) && !hiddenCanisters.has(canisterId)
    );
  }

  function visibilitySectionOpen(name: string): boolean {
    return expandedVisibilitySections[name] !== false;
  }

  function toggleVisibilitySection(name: string): void {
    expandedVisibilitySections = {
      ...expandedVisibilitySections,
      [name]: !visibilitySectionOpen(name),
    };
  }

  function setSectionVisible(name: string, visible: boolean): void {
    const next = new Set(hiddenSections);
    if (visible) next.delete(name);
    else next.add(name);
    hiddenSections = next;
  }

  function setStandVisible(section: string, stand: string, visible: boolean): void {
    const key = standVisibilityKey(section, stand);
    const next = new Set(hiddenStands);
    if (visible) next.delete(key);
    else next.add(key);
    hiddenStands = next;
  }

  function setCanisterVisible(canisterId: string, visible: boolean): void {
    const next = new Set(hiddenCanisters);
    if (visible) next.delete(canisterId);
    else next.add(canisterId);
    hiddenCanisters = next;
  }

  function principalVisible(principal: string): boolean {
    return !hiddenPrincipals.has(principal);
  }

  function setPrincipalVisible(principal: string, visible: boolean): void {
    const next = new Set(hiddenPrincipals);
    if (visible) next.delete(principal);
    else next.add(principal);
    hiddenPrincipals = next;
  }

  function toggleNodeVisibility(node: ControlNode): void {
    const cid = node.canister?.canister_id;
    if (cid) {
      setCanisterVisible(cid, hiddenCanisters.has(cid));
      return;
    }
    if (node.principal) {
      setPrincipalVisible(node.principal, hiddenPrincipals.has(node.principal));
    }
  }

  function onNodeDoubleClick(node: ControlNode, event: MouseEvent): void {
    if (Date.now() < suppressNodeDblClickUntil) return;
    if (!node.canister?.canister_id && !node.principal) return;
    event.preventDefault();
    event.stopPropagation();
    toggleNodeVisibility(node);
  }

  function showAllScopes(): void {
    hiddenSections = new Set();
    hiddenStands = new Set();
    hiddenCanisters = new Set();
    hiddenPrincipals = new Set();
  }

  function hideAllScopes(): void {
    hiddenSections = new Set(tree.sections.map((s) => s.name));
    hiddenStands = new Set(
      tree.sections.flatMap((s) => s.stands.map((d) => standVisibilityKey(s.name, d.name))),
    );
    hiddenCanisters = new Set(
      tree.sections.flatMap((s) =>
        s.stands.flatMap((d) => d.canisters.map((c) => c.canister_id).filter(Boolean)),
      ),
    );
    hiddenPrincipals = new Set(principalNodes.map((n) => n.principal ?? '').filter(Boolean));
  }

  const anyScopeHidden = $derived(
    hiddenSections.size > 0 ||
      hiddenStands.size > 0 ||
      hiddenCanisters.size > 0 ||
      hiddenPrincipals.size > 0,
  );

  function zoomBy(delta: number): void {
    zoom = clampControlGraphZoom(zoom + delta);
  }

  function currentView(name: string): ControlGraphView {
    return serializeControlGraphView({
      name,
      zoom,
      edgeTypes,
      hiddenSections,
      hiddenStands,
      hiddenCanisters,
      hiddenPrincipals,
      positions: customPositions,
    });
  }

  function applyView(view: ControlGraphView): void {
    edgeTypes = { ...view.edgeTypes };
    hiddenSections = new Set(view.hidden.sections);
    hiddenStands = new Set(view.hidden.stands);
    hiddenCanisters = new Set(view.hidden.canisters);
    hiddenPrincipals = new Set(view.hidden.principals);
    zoom = clampControlGraphZoom(view.zoom);
    customPositions = { ...view.positions };
  }

  function readStoredViews(): Record<string, ControlGraphView> {
    if (typeof localStorage === 'undefined') return {};
    try {
      const raw = localStorage.getItem(VIEWS_STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const out: Record<string, ControlGraphView> = {};
      for (const [name, value] of Object.entries(parsed)) {
        try {
          out[name] = parseControlGraphView(JSON.stringify(value));
        } catch {
          /* skip unreadable entry */
        }
      }
      return out;
    } catch {
      return {};
    }
  }

  function writeStoredViews(views: Record<string, ControlGraphView>): void {
    savedViews = views;
    if (typeof localStorage === 'undefined') return;
    try {
      localStorage.setItem(VIEWS_STORAGE_KEY, JSON.stringify(views));
    } catch {
      /* quota or private mode; in-memory copy still works */
    }
  }

  $effect(() => {
    savedViews = readStoredViews();
  });

  function saveView(): void {
    const name = viewNameInput.trim() || selectedViewName.trim();
    if (!name) return;
    writeStoredViews({ ...savedViews, [name]: currentView(name) });
    selectedViewName = name;
    viewNameInput = '';
  }

  function loadSelectedView(): void {
    const view = savedViews[selectedViewName];
    if (view) applyView(view);
  }

  function deleteSelectedView(): void {
    if (!selectedViewName) return;
    const next = { ...savedViews };
    delete next[selectedViewName];
    writeStoredViews(next);
    selectedViewName = '';
  }

  function downloadView(): void {
    const name = viewNameInput.trim() || selectedViewName.trim() || 'control-graph-view';
    const blob = new Blob([JSON.stringify(currentView(name), null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${name.replace(/[^a-z0-9._-]+/gi, '-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function uploadView(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    try {
      const view = parseControlGraphView(await file.text());
      applyView(view);
      writeStoredViews({ ...savedViews, [view.name]: view });
      selectedViewName = view.name;
    } catch (e) {
      uploadError = e instanceof Error ? e.message : String(e);
    } finally {
      input.value = '';
    }
  }

  let uploadError = $state('');

  const savedViewNames = $derived(Object.keys(savedViews).sort((a, b) => a.localeCompare(b)));

  function nodeIdLine(node: ControlNode): string {
    if (node.principal) return shortPrincipal(node.principal);
    if (node.canister?.canister_id) return shortPrincipal(node.canister.canister_id);
    return '';
  }

  // Self-loops are not drawn, so surface self-control on hover instead.
  function isSelfControlled(node: ControlNode): boolean {
    const id = node.canister?.canister_id;
    return Boolean(id && (node.canister?.controllers ?? []).includes(id));
  }

  function nodeStyle(node: ControlNode): { fill: string; stroke: string } {
    const c = node.canister;
    if (!c) return { fill: '#f8fafc', stroke: '#cbd5e1' };
    if (isCasalsCanister(c)) return { fill: '#eef2ff', stroke: '#a5b4fc' };
    if (isMultisigCanister(c)) return { fill: '#ecfdf5', stroke: '#6ee7b7' };
    if (isBatonCanister(c)) return { fill: '#fff7ed', stroke: '#fdba74' };
    if (c.kind === 'frontend') return { fill: '#eff6ff', stroke: '#bfdbfe' };
    return { fill: '#f5f3ff', stroke: '#ddd6fe' };
  }

  function clientToSvg(clientX: number, clientY: number): NodePosition {
    if (!svgEl) return { x: clientX, y: clientY };
    const pt = svgEl.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const ctm = svgEl.getScreenCTM();
    if (!ctm) return { x: clientX, y: clientY };
    const p = pt.matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  }

  function onNodePointerDown(nodeId: string, event: PointerEvent): void {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();

    const pos = displayPositions.get(nodeId);
    if (!pos) return;

    const pointerId = event.pointerId;
    const startClient = { x: event.clientX, y: event.clientY };
    const svgStart = clientToSvg(event.clientX, event.clientY);
    const offsetX = svgStart.x - pos.x;
    const offsetY = svgStart.y - pos.y;
    let dragging = false;

    const finish = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      draggingId = null;
    };

    const onMove = (moveEvent: PointerEvent) => {
      if (moveEvent.pointerId !== pointerId) return;
      if (!dragging) {
        const dx = moveEvent.clientX - startClient.x;
        const dy = moveEvent.clientY - startClient.y;
        if (Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
        dragging = true;
        draggingId = nodeId;
      }
      const p = clientToSvg(moveEvent.clientX, moveEvent.clientY);
      customPositions = {
        ...customPositions,
        [nodeId]: { x: p.x - offsetX, y: p.y - offsetY },
      };
    };

    const onUp = (upEvent: PointerEvent) => {
      if (upEvent.pointerId !== pointerId) return;
      if (dragging) suppressNodeDblClickUntil = Date.now() + 400;
      finish();
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  }

  function toggleLayer(key: keyof ControlGraphLayers): void {
    const enabled = layerGroupEnabled(edgeTypes, key);
    edgeTypes = setLayerGroupVisibility(edgeTypes, key, !enabled);
  }

  function toggleEdgeType(type: ControlEdgeType): void {
    edgeTypes = { ...edgeTypes, [type]: !edgeTypes[type] };
  }
</script>

<svelte:window bind:innerWidth={containerWidth} onkeydown={handleKeydown} />

<div
  bind:this={panelEl}
  class="w-full {isExpanded ? 'fixed inset-0 z-50 flex flex-col bg-white p-4 overflow-hidden' : ''}"
>
  {#if graph.nodes.length === 0 && fullGraph.nodes.length === 0}
    <div class="flex items-center justify-center text-sm text-primary-400 py-16">
      No control relationships to graph — enable a layer or refresh orchestration status.
    </div>
  {:else}
    <div class="mb-4 rounded-lg border border-[var(--color-border-primary)] bg-primary-50/60 px-3 py-2.5 space-y-2 shrink-0">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="text-[10px] font-semibold uppercase tracking-wider text-primary-500">Edge layers</div>
        <div class="flex items-center gap-1.5">
          {#if onRefreshControllers}
            <button
              type="button"
              class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50 disabled:opacity-50"
              title="Fetch live IC controllers for all canisters"
              disabled={controllersRefreshing}
              onclick={() => void onRefreshControllers()}
            >
              {controllersRefreshing ? 'Refreshing…' : 'Refresh controllers'}
            </button>
          {/if}
          <div class="inline-flex items-center rounded-md border border-primary-200 bg-white overflow-hidden">
            <button
              type="button"
              class="px-2 py-1 text-[11px] font-medium text-primary-600 hover:bg-primary-50"
              aria-label="Zoom out"
              onclick={() => zoomBy(-CONTROL_GRAPH_ZOOM_STEP)}
            >
              −
            </button>
            <button
              type="button"
              class="px-1.5 py-1 text-[10px] font-mono text-primary-500 border-x border-primary-200 hover:bg-primary-50"
              title="Reset zoom to 100%"
              onclick={() => (zoom = 1)}
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              class="px-2 py-1 text-[11px] font-medium text-primary-600 hover:bg-primary-50"
              aria-label="Zoom in"
              onclick={() => zoomBy(CONTROL_GRAPH_ZOOM_STEP)}
            >
              +
            </button>
          </div>
          <button
            type="button"
            class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50"
            onclick={resetLayout}
            disabled={Object.keys(customPositions).length === 0}
          >
            Reset layout
          </button>
          <button
            type="button"
            class="p-1.5 rounded-md border border-primary-200 bg-white text-primary-600 hover:bg-primary-50"
            aria-label={isExpanded ? 'Exit full screen (Esc)' : 'Full screen'}
            onclick={toggleExpanded}
          >
            {#if isExpanded}
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M9 9V4.5M9 9H4.5M9 9 3.75 3.75M15 9h4.5M15 9V4.5M15 9l5.25-5.25M9 15v4.5M9 15H4.5M9 15l-5.25 5.25M15 15h4.5M15 15v4.5m0-4.5 5.25 5.25" />
              </svg>
            {:else}
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <path stroke-linecap="round" stroke-linejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
              </svg>
            {/if}
          </button>
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                 {layerGroupEnabled(edgeTypes, 'icControllers') ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-primary-600 border-primary-200'}"
          onclick={() => toggleLayer('icControllers')}
        >
          IC controllers
        </button>
        <button
          type="button"
          class="px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                 {layerGroupEnabled(edgeTypes, 'commanders') ? 'bg-slate-600 text-white border-slate-600' : 'bg-white text-primary-600 border-primary-200'}"
          onclick={() => toggleLayer('commanders')}
        >
          Commanders
        </button>
        <button
          type="button"
          class="px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                 {layerGroupEnabled(edgeTypes, 'baton') ? 'bg-orange-600 text-white border-orange-600' : 'bg-white text-primary-600 border-primary-200'}"
          onclick={() => toggleLayer('baton')}
        >
          Baton
        </button>
      </div>
      <div class="flex flex-wrap gap-2 text-[11px]">
        {#each Object.entries(CONTROL_EDGE_META) as [type, meta] (type)}
          {@const enabled = edgeTypes[type as ControlEdgeType]}
          <button
            type="button"
            class="inline-flex items-center gap-1.5 px-2 py-1 rounded-md border transition-colors
                   {enabled
              ? 'bg-white border-primary-200 text-primary-700 hover:bg-primary-50'
              : 'bg-primary-100/80 border-primary-100 text-primary-400 line-through'}
                   {hoveredLegendType === type ? 'ring-2 ring-primary-300' : ''}"
            title={meta.tooltip}
            aria-label="{enabled ? 'Hide' : 'Show'} {meta.label}"
            aria-pressed={enabled}
            onmouseenter={() => (hoveredLegendType = type as ControlEdgeType)}
            onmouseleave={() => (hoveredLegendType = null)}
            onfocus={() => (hoveredLegendType = type as ControlEdgeType)}
            onblur={() => (hoveredLegendType = null)}
            onclick={() => toggleEdgeType(type as ControlEdgeType)}
          >
            <svg width="28" height="8" aria-hidden="true" class="{enabled ? '' : 'opacity-40'}">
              <line
                x1="0"
                y1="4"
                x2="28"
                y2="4"
                stroke={meta.stroke}
                stroke-width={meta.width ?? 1.5}
                stroke-dasharray={meta.dash ?? undefined}
              />
            </svg>
            {meta.label}
            {#if meta.legendScope}
              <span class="text-primary-400 font-normal">· {meta.legendScope}</span>
            {/if}
          </button>
        {/each}
      </div>

      <div class="flex flex-wrap items-center gap-1.5 pt-1.5 border-t border-[var(--color-border-primary)]/60">
        <span class="text-[10px] font-semibold uppercase tracking-wider text-primary-500 mr-1">Views</span>
        <select
          class="px-1.5 py-1 rounded-md text-[11px] border border-primary-200 bg-white text-primary-700 max-w-[11rem]"
          aria-label="Saved views"
          bind:value={selectedViewName}
        >
          <option value="">— saved views —</option>
          {#each savedViewNames as name (name)}
            <option value={name}>{name}</option>
          {/each}
        </select>
        <button
          type="button"
          class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50 disabled:opacity-40"
          disabled={!selectedViewName}
          onclick={loadSelectedView}
        >
          Load
        </button>
        <button
          type="button"
          class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50 disabled:opacity-40"
          disabled={!selectedViewName}
          onclick={deleteSelectedView}
        >
          Delete
        </button>
        <input
          class="px-1.5 py-1 rounded-md text-[11px] border border-primary-200 bg-white text-primary-700 w-32"
          placeholder="New view name"
          aria-label="View name"
          bind:value={viewNameInput}
        />
        <button
          type="button"
          class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50 disabled:opacity-40"
          title="Save visibility, edge layers, zoom, and node positions"
          disabled={!viewNameInput.trim() && !selectedViewName}
          onclick={saveView}
        >
          Save
        </button>
        <button
          type="button"
          class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50"
          onclick={downloadView}
        >
          Download
        </button>
        <button
          type="button"
          class="px-2 py-1 rounded-md text-[11px] font-medium border border-primary-200 bg-white text-primary-600 hover:bg-primary-50"
          onclick={() => fileInputEl?.click()}
        >
          Upload
        </button>
        <input
          bind:this={fileInputEl}
          type="file"
          accept="application/json,.json"
          class="hidden"
          onchange={uploadView}
        />
        {#if uploadError}
          <span class="text-[10px] text-red-600">{uploadError}</span>
        {/if}
      </div>
    </div>

    {#if graph.nodes.length === 0}
      <div class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
        {#if fullGraph.nodes.length === 0}
          No control relationships to graph — enable an edge type or refresh orchestration status.
        {:else}
          Nothing visible — turn on edge types above and/or use Show all for sections, stands, canisters, and principals.
        {/if}
      </div>
    {/if}

    {#if fullGraph.warnings.length}
      <div class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 space-y-1 shrink-0">
        {#each fullGraph.warnings as warning (warning)}
          <div>{warning}</div>
        {/each}
      </div>
    {/if}

    <div class="flex {isExpanded ? 'flex-1 min-h-0 gap-4' : 'flex-col gap-4'}">
      <aside
        class="rounded-lg border border-[var(--color-border-primary)] bg-primary-50/40 shrink-0 overflow-hidden flex flex-col
               {isExpanded ? 'w-72' : 'w-full'}"
        style={isExpanded ? `width:${VISIBILITY_SIDEBAR_WIDTH}px` : undefined}
      >
        <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-[var(--color-border-primary)] bg-white/80">
          <button
            type="button"
            class="flex items-center gap-1.5 min-w-0 text-left text-[10px] font-semibold uppercase tracking-wider text-primary-500"
            onclick={() => (visibilityOpen = !visibilityOpen)}
          >
            <svg
              class="w-3.5 h-3.5 shrink-0 transition-transform {visibilityOpen ? 'rotate-90' : ''}"
              fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
            >
              <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
            </svg>
            <span>Show / hide</span>
          </button>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="text-[10px] font-medium text-primary-500 hover:text-primary-700 disabled:opacity-40"
              onclick={showAllScopes}
              disabled={!anyScopeHidden}
            >
              Show all
            </button>
            <button
              type="button"
              class="text-[10px] font-medium text-primary-500 hover:text-primary-700"
              onclick={hideAllScopes}
            >
              Hide all
            </button>
          </div>
        </div>
        {#if visibilityOpen}
          <div class="overflow-y-auto px-2 py-2 space-y-1 {isExpanded ? 'max-h-none flex-1 min-h-0' : 'max-h-56'}">
            {#if principalNodes.length}
              <div class="rounded-md border border-[var(--color-border-primary)]/60 bg-white mb-2">
                <div class="flex items-center gap-2 px-2 py-1.5">
                  <button
                    type="button"
                    class="p-0.5 text-primary-400"
                    aria-label="{principalsOpen ? 'Collapse' : 'Expand'} principals"
                    onclick={() => (principalsOpen = !principalsOpen)}
                  >
                    <svg
                      class="w-3 h-3 transition-transform {principalsOpen ? 'rotate-90' : ''}"
                      fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                    </svg>
                  </button>
                  <span class="flex-1 min-w-0 text-xs font-semibold text-primary-800">Principals</span>
                  <span class="text-[10px] text-primary-400">{principalNodes.length}</span>
                </div>
                {#if principalsOpen}
                  <div class="border-t border-[var(--color-border-primary)]/50 px-2 py-1 space-y-0.5">
                    {#each principalNodes as node (node.id)}
                      {@const principal = node.principal ?? ''}
                      {@const principalOn = principalVisible(principal)}
                      <div class="flex items-center gap-2 py-0.5 pl-1">
                        <span class="flex-1 min-w-0 text-[10px] text-primary-600 truncate font-mono" title={principal}>
                          {node.label}
                        </span>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={principalOn}
                          aria-label="{principalOn ? 'Hide' : 'Show'} principal {node.label}"
                          class="relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors
                                 {principalOn ? 'bg-sky-600' : 'bg-primary-200'}"
                          onclick={() => setPrincipalVisible(principal, !principalOn)}
                        >
                          <span
                            class="inline-block h-2.5 w-2.5 transform rounded-full bg-white shadow transition-transform
                                   {principalOn ? 'translate-x-3' : 'translate-x-0.5'}"
                          ></span>
                        </button>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>
            {/if}
            {#each tree.sections as section (section.name)}
              {@const secOn = sectionVisible(section.name)}
              <div class="rounded-md border border-[var(--color-border-primary)]/60 bg-white">
                <div class="flex items-center gap-2 px-2 py-1.5">
                  <button
                    type="button"
                    class="p-0.5 text-primary-400"
                    aria-label="{visibilitySectionOpen(section.name) ? 'Collapse' : 'Expand'} {section.name}"
                    onclick={() => toggleVisibilitySection(section.name)}
                  >
                    <svg
                      class="w-3 h-3 transition-transform {visibilitySectionOpen(section.name) ? 'rotate-90' : ''}"
                      fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
                    >
                      <path stroke-linecap="round" stroke-linejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
                    </svg>
                  </button>
                  <span class="flex-1 min-w-0 text-xs font-semibold text-primary-800 truncate">{section.name}</span>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={secOn}
                    aria-label="{secOn ? 'Hide' : 'Show'} section {section.name}"
                    class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors
                           {secOn ? 'bg-indigo-600' : 'bg-primary-200'}"
                    onclick={() => setSectionVisible(section.name, !secOn)}
                  >
                    <span
                      class="inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform
                             {secOn ? 'translate-x-4' : 'translate-x-0.5'}"
                    ></span>
                  </button>
                </div>
                {#if visibilitySectionOpen(section.name)}
                  <div class="border-t border-[var(--color-border-primary)]/50 px-2 py-1 space-y-1">
                    {#each section.stands as stand (stand.name)}
                      {@const standOn = standVisible(section.name, stand.name)}
                      <div class="rounded border border-[var(--color-border-primary)]/40 bg-primary-50/30">
                        <div class="flex items-center gap-2 px-2 py-1">
                          <span class="flex-1 min-w-0 text-[11px] font-medium text-primary-700 truncate pl-3">{stand.name}</span>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={standOn}
                            aria-label="{standOn ? 'Hide' : 'Show'} stand {stand.name}"
                            class="relative inline-flex h-4 w-7 shrink-0 items-center rounded-full transition-colors
                                   {standOn ? 'bg-slate-600' : 'bg-primary-200'}
                                   {!secOn ? 'opacity-40' : ''}"
                            disabled={!secOn}
                            onclick={() => setStandVisible(section.name, stand.name, !standOn)}
                          >
                            <span
                              class="inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform
                                     {standOn ? 'translate-x-3' : 'translate-x-0.5'}"
                            ></span>
                          </button>
                        </div>
                        {#if stand.canisters.length}
                          <div class="border-t border-[var(--color-border-primary)]/30 px-2 py-1 space-y-0.5">
                            {#each stand.canisters as canister (canister.canister_id)}
                              {@const cid = canister.canister_id}
                              {@const canOn = cid ? canisterVisible(section.name, stand.name, cid) : false}
                              {#if cid}
                                <div class="flex items-center gap-2 py-0.5 pl-4">
                                  <span class="flex-1 min-w-0 text-[10px] text-primary-600 truncate" title={cid}>
                                    {canister.name}
                                    <span class="text-primary-400"> · {canister.kind}</span>
                                  </span>
                                  <button
                                    type="button"
                                    role="switch"
                                    aria-checked={canOn}
                                    aria-label="{canOn ? 'Hide' : 'Show'} canister {canister.name}"
                                    class="relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors
                                           {canOn ? 'bg-violet-600' : 'bg-primary-200'}
                                           {!standOn ? 'opacity-40' : ''}"
                                    disabled={!standOn}
                                    onclick={() => setCanisterVisible(cid, !canOn)}
                                  >
                                    <span
                                      class="inline-block h-2.5 w-2.5 transform rounded-full bg-white shadow transition-transform
                                             {canOn ? 'translate-x-3' : 'translate-x-0.5'}"
                                    ></span>
                                  </button>
                                </div>
                              {/if}
                            {/each}
                          </div>
                        {/if}
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {/if}
      </aside>

      <div bind:this={graphAreaEl} class="flex-1 min-w-0 min-h-0 flex flex-col">
        <div class="overflow-auto rounded-lg border border-[var(--color-border-primary)] bg-white {isExpanded ? 'flex-1 min-h-0' : ''}">
          <svg
            bind:this={svgEl}
            width={frozenViewport.width * zoom}
            height={frozenViewport.height * zoom}
            viewBox="0 0 {frozenViewport.width} {frozenViewport.height}"
            class="block touch-none select-none"
            style="touch-action: none;"
            role="img"
            aria-label="Orchestra control graph"
          >
            <defs>
              {#each Object.entries(CONTROL_EDGE_META) as [type, meta] (type)}
                <marker
                  id="arrow-{type}"
                  markerWidth="8"
                  markerHeight="8"
                  refX="7"
                  refY="4"
                  orient="auto"
                >
                  <path d="M0,0 L8,4 L0,8 Z" fill={meta.stroke} />
                </marker>
              {/each}
            </defs>

            {#each graph.edges as edge (edge.id)}
              {@const fromPos = displayPositions.get(edge.from)}
              {@const toPos = displayPositions.get(edge.to)}
              {@const meta = CONTROL_EDGE_META[edge.type]}
              {#if fromPos && toPos}
                {@const anchors = edgeAnchors(fromPos, toPos, CONTROL_NODE_WIDTH, CONTROL_NODE_HEIGHT)}
                <path
                  d={edgePathBetween(anchors)}
                  fill="none"
                  stroke={meta.stroke}
                  stroke-width={hoveredEdge?.id === edge.id ? (meta.width ?? 1.5) + 1 : (meta.width ?? 1.5)}
                  stroke-dasharray={meta.dash}
                  marker-end="url(#arrow-{edge.type})"
                  opacity={hoveredEdge && hoveredEdge.id !== edge.id ? 0.25 : 0.85}
                  class="transition-opacity pointer-events-stroke"
                  onmouseenter={() => (hoveredEdge = edge)}
                  onmouseleave={() => (hoveredEdge = null)}
                >
                  <title>{meta.tooltip}{edge.label ? ` (${edge.label})` : ''}</title>
                </path>
              {/if}
            {/each}

            {#each graph.nodes as node (node.id)}
              {@const pos = displayPositions.get(node.id)}
              {#if pos}
                {@const style = nodeStyle(node)}
                {@const isDragging = draggingId === node.id}
                <g
                  transform="translate({pos.x - CONTROL_NODE_WIDTH / 2}, {pos.y - CONTROL_NODE_HEIGHT / 2})"
                  class="cursor-grab {isDragging ? 'cursor-grabbing' : ''} {node.canister || node.principal ? 'cursor-pointer' : ''}"
                  onpointerdown={(e) => onNodePointerDown(node.id, e)}
                  ondblclick={(e) => onNodeDoubleClick(node, e)}
                  onmouseenter={() => (hoveredNode = node)}
                  onmouseleave={() => (hoveredNode = null)}
                  aria-label="{node.label} node"
                >
                  <rect
                    width={CONTROL_NODE_WIDTH}
                    height={CONTROL_NODE_HEIGHT}
                    rx="8"
                    fill={style.fill}
                    stroke={isDragging ? '#334155' : style.stroke}
                    stroke-width={isDragging ? 2.5 : 2}
                    opacity={hoveredEdge && !graph.edges.some((e) => (e.from === node.id || e.to === node.id) && e.id === hoveredEdge.id) ? 0.55 : 1}
                  />
                  <foreignObject
                    x="8"
                    y="6"
                    width={CONTROL_NODE_WIDTH - 16}
                    height={CONTROL_NODE_HEIGHT - 12}
                    class="pointer-events-none"
                  >
                    <div
                      xmlns="http://www.w3.org/1999/xhtml"
                      class="flex flex-col justify-center h-full min-w-0 leading-tight"
                    >
                      <div class="text-[11px] font-semibold text-slate-800 break-words">{node.label}</div>
                      {#if nodeIdLine(node)}
                        <div class="text-[9px] font-mono text-slate-400 mt-0.5 truncate">{nodeIdLine(node)}</div>
                      {/if}
                    </div>
                  </foreignObject>
                </g>
              {/if}
            {/each}
          </svg>
        </div>

        <div class="mt-3 pt-2 border-t border-[var(--color-border-primary)] text-xs text-primary-500 min-h-[1.25rem] shrink-0">
          {#if hoveredEdge}
            <span class="text-primary-700">{CONTROL_EDGE_META[hoveredEdge.type].tooltip}</span>
          {:else if hoveredLegendType}
            <span class="text-primary-700">{CONTROL_EDGE_META[hoveredLegendType].tooltip}</span>
          {:else if hoveredNode}
            <span class="font-mono text-[11px] text-primary-700">
              {hoveredNode.label}
              {#if hoveredNode.canister?.canister_id}
                · {hoveredNode.canister.canister_id}
              {:else if hoveredNode.principal}
                · {shortPrincipal(hoveredNode.principal)}
              {/if}
              {#if hoveredNode.section && hoveredNode.stand}
                · {hoveredNode.section} / {hoveredNode.stand}
              {/if}
              {#if isSelfControlled(hoveredNode)}
                <span class="text-primary-500">· self-controlled (upgrades itself)</span>
              {/if}
            </span>
          {:else}
            <span>Hold and drag to move nodes. Double-click a canister or principal to show or hide it.</span>
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>
