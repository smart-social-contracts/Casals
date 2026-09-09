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
    graphLayoutSignature,
    filterControlGraph,
    filterControlGraphByEdgeTypes,
    layerGroupEnabled,
    setLayerGroupVisibility,
    standVisibilityKey,
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
  }

  let {
    tree,
    orchestrationStatus = null,
    casalsBackendId = '',
    principalLabel,
  }: Props = $props();

  let edgeTypes = $state<ControlEdgeTypeVisibility>({ ...DEFAULT_CONTROL_EDGE_TYPE_VISIBILITY });
  let hoveredEdge = $state<ControlEdge | null>(null);
  let hoveredNode = $state<ControlNode | null>(null);
  let containerWidth = $state(960);
  let customPositions = $state<Record<string, NodePosition>>({});
  let layoutSignature = $state('');
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

  const DRAG_THRESHOLD_PX = 5;
  const VISIBILITY_SIDEBAR_WIDTH = 288;

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

  $effect(() => {
    const sig = graphLayoutSignature(graph, layoutWidth);
    if (sig !== layoutSignature) {
      layoutSignature = sig;
      customPositions = {};
      frozenViewport = frozenGraphViewport(autoPositions, layoutWidth);
    }
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

  const anyScopeHidden = $derived(
    hiddenSections.size > 0 ||
      hiddenStands.size > 0 ||
      hiddenCanisters.size > 0 ||
      hiddenPrincipals.size > 0,
  );

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
              : 'bg-primary-100/80 border-primary-100 text-primary-400 line-through'}"
            title="{enabled ? 'Hide' : 'Show'} {meta.tooltip}"
            aria-pressed={enabled}
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
          </button>
        {/each}
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
          <button
            type="button"
            class="text-[10px] font-medium text-primary-500 hover:text-primary-700 disabled:opacity-40"
            onclick={showAllScopes}
            disabled={!anyScopeHidden}
          >
            Show all
          </button>
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
            width={frozenViewport.width}
            height={frozenViewport.height}
            viewBox="0 0 {frozenViewport.width} {frozenViewport.height}"
            class="block max-w-full touch-none select-none"
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
                  <text x="10" y="22" fill="#1e293b" class="text-[11px] font-semibold pointer-events-none">
                    {node.label.length > 18 ? `${node.label.slice(0, 16)}…` : node.label}
                  </text>
                  {#if node.sublabel}
                    <text x="10" y="40" fill="#64748b" class="text-[9px] uppercase tracking-wide pointer-events-none">
                      {node.sublabel}
                    </text>
                  {/if}
                </g>
              {/if}
            {/each}
          </svg>
        </div>

        <div class="mt-3 pt-2 border-t border-[var(--color-border-primary)] text-xs text-primary-500 min-h-[1.25rem] shrink-0">
          {#if hoveredEdge}
            <span class="text-primary-700">{CONTROL_EDGE_META[hoveredEdge.type].tooltip}</span>
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
            </span>
          {:else}
            <span>Hold and drag to move nodes. Double-click a canister or principal to show or hide it.</span>
          {/if}
        </div>
      </div>
    </div>
  {/if}
</div>
